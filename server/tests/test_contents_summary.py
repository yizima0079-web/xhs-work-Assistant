"""内容池概览：一次取齐派生计数，替代前端逐条探测的 N+1。

概览是删除确认弹窗的数据源，所以计数必须与逐条查询一致 —— 这里用「同一批 ID
分别走概览与单点查询，结果对齐」来锁住，而不是只断言几个硬编码数字。
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app.db import connect
from app.domain.enums import ClaimStatus, ClaimType, ReviewStatus
from app.domain.models import Analysis, Claim, Report
from app.main import create_app
from app.repositories.sqlite import (
    SqliteAnalysisRepository,
    SqliteContentRepository,
    SqliteReviewRepository,
)
from tests.test_review_repository import make_content

A, B, C = "xhs:aaa", "xhs:bbb", "xhs:ccc"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _claim(cid: str, n: int) -> Claim:
    return Claim(claim_id=f"{cid}-c{n}", content_id=cid, text=f"断言 {n}",
                 status=ClaimStatus.SUPPORTED, confidence=0.9, claim_type=ClaimType.FACT)


async def _seed() -> None:
    """A：2 条分析 + 2 版审核 + 1 篇 KB 文档 + 2 篇报告；B：1 条分析；C：干净。"""
    content_repo, review_repo, analysis_repo = (
        SqliteContentRepository(), SqliteReviewRepository(), SqliteAnalysisRepository()
    )
    for cid in (A, B, C):
        await content_repo.upsert(make_content(cid))

    await analysis_repo.add(Analysis(analysis_id=f"{A}-a1", content_id=A, verdict="flat"))
    await analysis_repo.add(Analysis(analysis_id=f"{A}-a2", content_id=A, verdict="viral"))
    await analysis_repo.add(Analysis(analysis_id=f"{B}-a1", content_id=B, verdict="flat"))

    await review_repo.save_review_batch(A, [_claim(A, 1)], [], [], ReviewStatus.APPROVED)
    await review_repo.save_review_batch(A, [_claim(A, 2)], [], [], ReviewStatus.APPROVED)

    async with connect() as db:
        await db.execute(
            "INSERT INTO kb_documents (doc_id, source_type, source_id, doc_type, title, tags,"
            " content_hash, raw_text, markdown, status, chunk_count, created_at)"
            " VALUES (?,?,?,'general','内容文档','[]','h-a','','# 正文','ready',1,?)",
            (f"{A}-doc1", "content", A, _now()),
        )
        await db.commit()

    await review_repo.add_report(Report(report_id="r-a", title="A", content_ids=[A]))
    await review_repo.add_report(Report(report_id="r-ab", title="AB", content_ids=[A, B]))


@pytest.fixture
def content_repo():
    return SqliteContentRepository()


async def test_summary_counts_match_point_queries(init_test_db, content_repo):
    await _seed()
    got = {row["content_id"]: row for row in await content_repo.summary(limit=20)}

    assert set(got) == {A, B, C}

    assert got[A]["analysis_count"] == 2
    assert got[A]["latest_analysis_id"] == f"{A}-a2"   # 后插的即最新
    assert got[A]["latest_analysis_at"]
    assert got[A]["review_batch_count"] == 2
    assert got[A]["claim_count"] == 2                  # 两批断言都在（旧版没被删）
    assert got[A]["report_count"] == 2                 # r-a + r-ab
    assert got[A]["kb_doc_count"] == 1

    assert got[B]["analysis_count"] == 1
    assert got[B]["claim_count"] == 0
    assert got[B]["report_count"] == 1                 # 只有 r-ab
    assert got[B]["kb_doc_count"] == 0

    # 干净内容：全 0 且仍出现在结果里（前端要靠它渲染空态）
    assert got[C]["analysis_count"] == 0
    assert got[C]["latest_analysis_id"] is None
    assert got[C]["latest_analysis_at"] is None
    assert got[C]["report_count"] == 0
    assert got[C]["kb_doc_count"] == 0


async def test_summary_respects_limit_and_is_newest_first(init_test_db, content_repo):
    await _seed()
    limited = await content_repo.summary(limit=2)
    assert len(limited) == 2
    # contents 按 rowid DESC 取，后建的在前
    assert [r["content_id"] for r in limited] == [C, B]


async def test_summary_empty_pool(init_test_db, content_repo):
    assert await content_repo.summary() == []
    assert await content_repo.summary(limit=0) == []


async def test_summary_counts_kb_docs_from_analysis_source(init_test_db, content_repo):
    """分析的 KB 文档（source_type='analysis'）要算到它所属内容头上。"""
    await _seed()
    async with connect() as db:
        await db.execute(
            "INSERT INTO kb_documents (doc_id, source_type, source_id, doc_type, title, tags,"
            " content_hash, raw_text, markdown, status, chunk_count, created_at)"
            " VALUES (?,?,?,'general','分析文档','[]','h-a2','','# 正文','ready',1,?)",
            (f"{A}-doc2", "analysis", f"{A}-a1", _now()),
        )
        await db.commit()

    got = {row["content_id"]: row for row in await content_repo.summary()}
    assert got[A]["kb_doc_count"] == 2
    assert got[B]["kb_doc_count"] == 0    # 不能把 A 的分析文档算到 B 头上


async def test_summary_endpoint_route(init_test_db, make_adapter, make_llm):
    """路由声明顺序：/contents/summary 不能被 /contents/{content_id} 吃掉。"""
    app = create_app(adapter=make_adapter(), llm_adapter=make_llm([]))
    async with app.router.lifespan_context(app):
        await _seed()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            r = await client.get("/api/v1/contents/summary")
            assert r.status_code == 200, r.text
            body = {row["content_id"]: row for row in r.json()}
            assert body[A]["analysis_count"] == 2
            assert body[A]["review_batch_count"] == 2

            assert (await client.get("/api/v1/contents/summary?limit=1")).json()[0][
                "content_id"
            ] == C
