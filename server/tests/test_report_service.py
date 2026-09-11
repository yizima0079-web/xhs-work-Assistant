"""报告服务测试：Markdown 渲染（纯函数）＋ ReportService.build_report 编排。"""
from __future__ import annotations

import pytest

from app.adapters.llm import ModelNotConfiguredError, ModelParseError
from app.domain.models import Content
from app.repositories.sqlite import SqliteContentRepository, SqliteReviewRepository
from app.schemas.report import ReportPayload, ReportScope
from app.services.reporting import ReportService, render_markdown


def make_payload() -> ReportPayload:
    return ReportPayload(
        report_id="rep-9",
        scope=ReportScope(platforms=["xhs"], content_ids=["xhs:a", "xhs:b"], time_range={"from": "t0"}),
        executive_summary="咖啡话题持续高热",
        viral_patterns=[
            {
                "pattern": "提神话术扎堆",
                "evidence_content_ids": ["xhs:a"],
                "confidence": 0.85,
                "counterexamples": ["xhs:b"],
            }
        ],
        presentation_style={"visual": ["大字标题"], "text": ["问句式开场"], "video": [], "interaction": []},
        account_persona={
            "hypotheses": ["偏向功能宣称型"],
            "evidence": ["xhs:a"],
            "confidence": 0.7,
        },
        trend_direction={"topics": ["咖啡"], "direction": "rising", "window": "近7天"},
        limitations=["本地样本有限"],
        source_refs=["https://example.com/a"],
    )


def test_render_markdown_covers_all_sections():
    md = render_markdown(make_payload().model_dump(), title="咖啡话题报告", created_at="2026-09-09")
    assert md.startswith("# 咖啡话题报告")
    for section in ["## 摘要", "## 爆点模式", "## 表现风格", "## 账号人设（假设）", "## 趋势", "## 局限", "## 引用来源", "## 审计原文"]:
        assert section in md, f"缺段落 {section}"
    assert "上升" in md          # rising 中文映射
    assert "85%" in md           # confidence 0.85 → 85%
    assert "`xhs:a`" in md
    assert "report_id" in md     # 审计原文内嵌整份 payload
    assert "```json" in md and md.rstrip().endswith("```")


def test_render_markdown_empty_payload_no_crash():
    payload = ReportPayload(report_id="r", scope=ReportScope()).model_dump()
    md = render_markdown(payload, title="空报告")
    assert "# 空报告" in md
    assert "（无）" in md


# ---- build_report 编排 ----

def make_content(cid: str, text: str, url: str | None = None) -> Content:
    p, i = cid.split(":", 1)
    return Content(content_id=cid, platform=p, platform_item_id=i, text=text, canonical_url=url)


async def _seed(init_test_db, docs):
    repo = SqliteContentRepository()
    for d in docs:
        await repo.upsert(d)
    return repo


def _service(content_repo, llm) -> ReportService:
    return ReportService(
        review_repo=SqliteReviewRepository(),
        content_repo=content_repo,
        llm=llm,
    )


def _raw_report(cid: str) -> dict:
    return {
        "executive_summary": "咖啡相关笔记在本轮样本中高热",
        "viral_patterns": [
            {
                "pattern": "提神功效宣称扎堆",
                "evidence_content_ids": [cid],
                "confidence": 0.8,
                "counterexamples": [],
            }
        ],
        "presentation_style": {"visual": ["大字标题"], "text": ["功效前置"], "video": [], "interaction": []},
        "account_persona": {"hypotheses": ["功能性宣称账号"], "evidence": [cid], "confidence": 0.6},
        "trend_direction": {"topics": ["咖啡"], "direction": "rising", "window": "近7天"},
        "limitations": ["仅为本地样本"],
    }


async def test_build_report_requires_key(init_test_db):
    content_repo = await _seed(init_test_db, [make_content("xhs:a", "文本")])
    with pytest.raises(ModelNotConfiguredError):
        await _service(content_repo, None).build_report(["xhs:a"])


async def test_build_report_missing_content(init_test_db, make_llm):
    content_repo = await _seed(init_test_db, [make_content("xhs:a", "文本")])
    with pytest.raises(KeyError):
        await _service(content_repo, make_llm()).build_report(["xhs:nope"])


async def test_build_report_happy_path_persists(init_test_db, make_llm):
    content_repo = await _seed(
        init_test_db,
        [
            make_content("xhs:a", "咖啡提神效果好，早上来一杯。", url="https://xhs/a"),
            make_content("xhs:b", "咖啡搭配运动效率更高。", url="https://xhs/b"),
        ],
    )
    llm = make_llm([_raw_report("xhs:a")])
    report = await _service(content_repo, llm).build_report(["xhs:a", "xhs:b"], title="咖啡样本报告")

    assert report.title == "咖啡样本报告"
    assert report.content_ids == ["xhs:a", "xhs:b"]
    assert report.schema_version == "1.0"

    # 净化后仅保留合法引用
    payload = report.payload
    assert payload["viral_patterns"][0]["evidence_content_ids"] == ["xhs:a"]
    assert payload["scope"]["content_ids"] == ["xhs:a", "xhs:b"]
    assert payload["scope"]["platforms"] == ["xhs"]
    assert payload["source_refs"] == ["https://xhs/a", "https://xhs/b"]
    assert payload["analysis_version"] == "1.0"
    # 样本 2 < 3 → 趋势 uncertain + limitations 追加说明
    assert payload["trend_direction"]["direction"] == "uncertain"
    assert any("uncertain" in lim for lim in payload["limitations"])

    # markdown 内嵌审计原文
    assert "## 审计原文" in report.markdown
    assert report.report_id in report.markdown

    # 落库可查
    got = await SqliteReviewRepository().get_report(report.report_id)
    assert got is not None
    assert got.markdown == report.markdown

    # 模型收到了样本摘录（防 prompt 里没有内容）
    user_content = llm.calls[0][1].content
    assert "xhs:a" in user_content and "咖啡提神效果好" in user_content


async def test_build_report_strips_fabricated_refs(init_test_db, make_llm):
    content_repo = await _seed(
        init_test_db,
        [
            make_content("xhs:a", "咖啡提神效果好。"),
            make_content("xhs:b", "咖啡运动搭档。"),
            make_content("xhs:c", "牛奶补钙助眠。"),
        ],
    )
    raw = _raw_report("xhs:a")
    raw["viral_patterns"].append(
        {"pattern": "编造模式", "evidence_content_ids": ["xhs:ghost"], "confidence": 0.9}
    )
    raw["account_persona"]["evidence"] = ["xhs:a", "xhs:ghost"]  # 混合 → 只留合法
    report = await _service(content_repo, make_llm([raw])).build_report(["xhs:a", "xhs:b", "xhs:c"])
    patterns = report.payload["viral_patterns"]
    # 全越界模式被删；合法模式保留
    assert [p["pattern"] for p in patterns] == ["提神功效宣称扎堆"]
    assert report.payload["account_persona"]["evidence"] == ["xhs:a"]


async def test_build_report_unparseable_structure_raises(init_test_db, make_llm):
    content_repo = await _seed(init_test_db, [make_content("xhs:a", "文本")])
    llm = make_llm(["不是对象"])  # FakeModel 允许返回非 dict → sanitize 抛 ReportSchemaError
    with pytest.raises(ModelParseError):
        await _service(content_repo, llm).build_report(["xhs:a"])
