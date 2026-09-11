"""审核/报告 API 集成测试：httpx ASGITransport + 生命周期内真实仓储 + FakeModel。"""
from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.domain.models import Content
from app.main import create_app
from app.repositories.sqlite import SqliteContentRepository

EXTRACT = {"claims": [{"text": "咖啡提神效果好", "claim_type": "fact", "confidence": 0.9}]}
JUDGE = {
    "status": "supported",
    "evidence": [{"content_id": "xhs:doc1", "supports": True, "strength": 0.9}],
    "rationale": "本地语料佐证", "confidence": 0.9,
}


def make_content(cid: str, text: str, url: str | None = None) -> Content:
    p, i = cid.split(":", 1)
    return Content(content_id=cid, platform=p, platform_item_id=i, text=text, canonical_url=url)


async def seed():
    repo = SqliteContentRepository()
    docs = [
        make_content("xhs:subject", "这款咖啡能提神，长期喝可能心悸。"),
        make_content("xhs:doc1", "很多人反馈咖啡提神效果明显。"),
        make_content("xhs:doc2", "咖啡运动前后喝都不错。", url="https://xhs/doc2"),
    ]
    for d in docs:
        await repo.upsert(d)


async def test_review_no_key_returns_503(init_test_db, make_adapter, monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", None)
    app = create_app(adapter=make_adapter(), llm_adapter=None)
    async with app.router.lifespan_context(app):
        await seed()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post("/api/v1/contents/xhs:subject/review")
            assert resp.status_code == 503


async def test_review_missing_content_404(init_test_db, make_adapter, make_llm):
    app = create_app(adapter=make_adapter(), llm_adapter=make_llm([]))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            resp = await client.post("/api/v1/contents/xhs:nope/review")
            assert resp.status_code == 404


async def test_review_flow_then_decision(init_test_db, make_adapter, make_llm):
    app = create_app(adapter=make_adapter(), llm_adapter=make_llm([EXTRACT, JUDGE]))
    async with app.router.lifespan_context(app):
        await seed()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            # 自动审核 → approved
            r = await client.post("/api/v1/contents/xhs:subject/review")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["review_status"] == "approved"
            assert body["claim_count"] == 1

            # 非 pending 重复自动审核 → 409
            r2 = await client.post("/api/v1/contents/xhs:subject/review")
            assert r2.status_code == 409

            # 内容审核详情
            r3 = await client.get("/api/v1/contents/xhs:subject/claims")
            assert r3.status_code == 200
            detail = r3.json()
            assert detail["content"]["content_id"] == "xhs:subject"
            claim_item = detail["claims"][0]
            assert claim_item["claim"]["status"] == "supported"
            assert claim_item["evidence"][0]["source_ref"] == "xhs:doc1"

            claim_id = claim_item["claim"]["claim_id"]
            evidence_id = claim_item["evidence"][0]["evidence_id"]

            # 单条断言快照
            r4 = await client.get(f"/api/v1/claims/{claim_id}")
            assert r4.status_code == 200
            assert r4.json()["claim"]["claim_id"] == claim_id

            # 人工改判 contradicted → content rejected（单 claim）
            r5 = await client.post(
                f"/api/v1/claims/{claim_id}/decision",
                json={"status": "contradicted", "rationale": "人工复核认为证据不足"},
            )
            assert r5.status_code == 200, r5.text
            assert r5.json()["review_status"] == "rejected"

            # 引用不属于该 claim 的 evidence → 422
            r6 = await client.post(
                f"/api/v1/claims/{claim_id}/decision",
                json={"status": "supported", "evidence_ids": ["not-mine"]},
            )
            assert r6.status_code == 422


async def test_claim_not_found_404(init_test_db, make_adapter, make_llm):
    app = create_app(adapter=make_adapter(), llm_adapter=make_llm([]))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            assert (await client.get("/api/v1/claims/nope")).status_code == 404
            assert (await client.post("/api/v1/claims/nope/decision", json={"status": "supported"})).status_code == 404


RAW_REPORT = {
    "executive_summary": "咖啡话题高热",
    "viral_patterns": [
        {"pattern": "提神功效宣称", "evidence_content_ids": ["xhs:subject"], "confidence": 0.8,
         "counterexamples": []}
    ],
    "presentation_style": {"visual": [], "text": [], "video": [], "interaction": []},
    "account_persona": {"hypotheses": ["功能宣称型"], "evidence": ["xhs:subject"], "confidence": 0.6},
    "trend_direction": {"topics": ["咖啡"], "direction": "rising", "window": "近7天"},
    "limitations": ["本地样本"],
}


async def test_report_flow(init_test_db, make_adapter, make_llm):
    app = create_app(adapter=make_adapter(), llm_adapter=make_llm([RAW_REPORT]))
    async with app.router.lifespan_context(app):
        await seed()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            r = await client.post(
                "/api/v1/reports",
                json={"content_ids": ["xhs:subject", "xhs:doc1", "xhs:doc2"], "title": "样本报告"},
            )
            assert r.status_code == 200, r.text
            report = r.json()
            assert report["title"] == "样本报告"
            assert report["content_ids"] == ["xhs:subject", "xhs:doc1", "xhs:doc2"]
            # 样本 3 → 趋势不被强制 uncertain（rising 保留）
            assert report["payload"]["trend_direction"]["direction"] == "rising"
            # 净化保留合法引用；markdown 内嵌审计原文
            assert report["payload"]["viral_patterns"][0]["evidence_content_ids"] == ["xhs:subject"]
            assert "## 审计原文" in report["markdown"]

            # 列表 & 详情
            rl = await client.get("/api/v1/reports")
            assert rl.status_code == 200
            assert any(x["report_id"] == report["report_id"] for x in rl.json())
            rg = await client.get(f"/api/v1/reports/{report['report_id']}")
            assert rg.status_code == 200
            assert rg.json()["report_id"] == report["report_id"]


async def test_report_missing_content_404(init_test_db, make_adapter, make_llm):
    app = create_app(adapter=make_adapter(), llm_adapter=make_llm([]))
    async with app.router.lifespan_context(app):
        await seed()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            r = await client.post("/api/v1/reports", json={"content_ids": ["xhs:ghost"]})
            assert r.status_code == 404


async def test_report_missing_report_404(init_test_db, make_adapter, make_llm):
    app = create_app(adapter=make_adapter(), llm_adapter=make_llm([]))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            assert (await client.get("/api/v1/reports/nope")).status_code == 404


async def test_force_rereview_adds_new_batch_and_404_is_avoided(init_test_db, make_adapter, make_llm):
    """?force=true 为重新审核：200 新开一版，而不是 409；默认读的是新的一版。"""
    app = create_app(adapter=make_adapter(), llm_adapter=make_llm([EXTRACT, JUDGE, EXTRACT, JUDGE]))
    async with app.router.lifespan_context(app):
        await seed()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            assert (await client.post("/api/v1/contents/xhs:subject/review")).status_code == 200

            first = await client.get("/api/v1/contents/xhs:subject/claims")
            old_ids = {c["claim"]["claim_id"] for c in first.json()["claims"]}
            old_batch = first.json()["batch_id"]

            r = await client.post("/api/v1/contents/xhs:subject/review?force=true")
            assert r.status_code == 200, r.text
            assert r.json()["claim_count"] == 1

            after = await client.get("/api/v1/contents/xhs:subject/claims")
            new_ids = {c["claim"]["claim_id"] for c in after.json()["claims"]}
            assert len(after.json()["claims"]) == 1        # 默认只回生效批次，不是叠加成 2 条
            assert not (old_ids & new_ids)                 # 确实是全新一批断言
            assert after.json()["claims"][0]["evidence"][0]["source_ref"] == "xhs:doc1"
            # 旧批次没被删：显式按 batch_id 仍能取回
            assert [b["batch_seq"] for b in after.json()["batches"]] == [2, 1]
            old = await client.get(
                f"/api/v1/contents/xhs:subject/claims?batch_id={old_batch}"
            )
            assert {c["claim"]["claim_id"] for c in old.json()["claims"]} == old_ids


async def test_reports_can_be_filtered_by_content(init_test_db, make_adapter, make_llm):
    """GET /reports?content_id= 只返回包含该内容的报告（单篇报告的「查看历史」）。"""
    app = create_app(adapter=make_adapter(), llm_adapter=make_llm([EXTRACT, JUDGE]))
    async with app.router.lifespan_context(app):
        await seed()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
            r = await client.post("/api/v1/reports", json={"content_ids": ["xhs:subject"], "title": "单篇"})
            assert r.status_code == 200, r.text

            hit = await client.get("/api/v1/reports?content_id=xhs:subject")
            assert hit.status_code == 200
            assert [x["title"] for x in hit.json()] == ["单篇"]

            miss = await client.get("/api/v1/reports?content_id=xhs:doc1")
            assert miss.status_code == 200 and miss.json() == []
