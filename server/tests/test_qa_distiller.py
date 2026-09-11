"""Q&A 蒸馏服务测试：三入口 → 净化 → 草稿落库；幂等/reused、force 只清 draft、无 key 503。"""
from __future__ import annotations

import pytest

from app.adapters.llm import ModelNotConfiguredError, ModelParseError
from app.domain.models import Analysis, Content
from app.repositories.sqlite import SqliteAnalysisRepository, SqliteContentRepository
from app.repositories.kb import SqliteKnowledgeBaseRepository
from app.schemas.qa import QaPairDraft
from app.services.qa_distiller import QaDistillerService, render_qa_markdown


def make_content(cid: str, title: str, text: str = "正文", tags: list[str] | None = None) -> Content:
    p, i = cid.split(":", 1)
    return Content(
        content_id=cid, platform=p, platform_item_id=i,
        title=title, text=text, tags=tags or [], author_name="作者A",
        canonical_url=f"https://xhs/{i}",
    )


def make_analysis(aid: str, content_id: str, compared_with: list[str] | None = None) -> Analysis:
    return Analysis(
        analysis_id=aid, content_id=content_id, verdict="viral",
        payload={"verdict": "viral"}, markdown="# 归因报告\n\n反差选材 + 自费实测推动互动。",
        compared_with=compared_with or [],
    )


def _raw(pairs: list[dict]) -> dict:
    return {"qa_pairs": pairs, "limitations": ["样本有限"]}


def _pair_dict(**kw) -> dict:
    base = {
        "question": "为什么这种夸张演绎能爆？",
        "answer": "结论 + 拆解 + 适用条件 + 失败风险。",
        "dimensions": {"technique": "夸张演绎", "persona": "素人人设", "transfer": ["换选题"]},
        "evidence": [],
        "tags": ["爆款拆解"],
        "confidence": 0.6,
    }
    base.update(kw)
    return base


async def _service(init_test_db, llm, contents, analyses) -> QaDistillerService:
    content_repo = SqliteContentRepository()
    for c in contents:
        await content_repo.upsert(c)
    analysis_repo = SqliteAnalysisRepository()
    for a in analyses:
        await analysis_repo.add(a)
    return QaDistillerService(
        kb_repo=SqliteKnowledgeBaseRepository(),
        content_repo=content_repo,
        analysis_repo=analysis_repo,
        llm=llm,
    )


async def test_distill_from_analysis_creates_pending_qa_doc(init_test_db, make_llm):
    base = make_content("xhs:base", "同话题基线")
    main = make_content("xhs:main", "AI 眼镜自费实测", tags=["AI眼镜"])
    llm = make_llm([_raw([_pair_dict(evidence=[
        {"source_kind": "analysis", "ref": "aid-1", "excerpt": "点赞 2177"},
        {"source_kind": "content", "ref": "xhs:main", "excerpt": "自费 2000 块"},
        {"source_kind": "content", "ref": "编造", "excerpt": "假"},
    ])])])
    svc = await _service(
        init_test_db, llm, [base, main], [make_analysis("aid-1", "xhs:main", ["xhs:base"])]
    )

    res = await svc.distill_from_analysis("aid-1")

    assert res.reused is False
    assert res.doc.doc_type == "qa" and res.doc.status == "pending" and res.doc.chunk_count == 0
    assert res.doc.source_type == "analysis" and res.doc.source_id == "aid-1"
    assert res.doc.title.startswith("爆款拆解 Q&A")
    assert len(res.pairs) == 1
    p = res.pairs[0]
    assert p.status == "draft" and p.qa_index == 0 and p.source_type == "distilled"
    assert p.source_id == "aid-1" and p.source_author == "作者A"
    assert [e["ref"] for e in p.evidence] == ["aid-1", "xhs:main"]  # 越界那条被丢
    assert res.dropped_refs == 1
    assert p.dimensions["technique"] == "夸张演绎"
    # 草稿未审核 → 不产生任何 chunk（未审核不入库）
    assert await SqliteKnowledgeBaseRepository().list_chunks_by_doc(res.doc.doc_id) == []
    # 容器文档 markdown 已渲染（供预览/溯源）
    doc = await SqliteKnowledgeBaseRepository().get_document(res.doc.doc_id)
    assert "Q1：为什么这种夸张演绎能爆？" in doc.markdown and "夸张演绎" in doc.markdown
    # prompt 里带上了真实素材与候选基线
    user = llm.calls[0][-1].content
    assert "xhs:base" in user and "归因报告" in user and "aid-1" in user


async def test_distill_is_idempotent_by_source(init_test_db, make_llm):
    main = make_content("xhs:main", "标题")
    llm = make_llm([_raw([_pair_dict()])])
    svc = await _service(init_test_db, llm, [main], [make_analysis("aid-1", "xhs:main")])

    first = await svc.distill_from_analysis("aid-1")
    second = await svc.distill_from_analysis("aid-1")

    assert second.reused is True and second.doc.doc_id == first.doc.doc_id
    assert len(second.pairs) == 1
    assert len(llm.calls) == 1  # 幂等命中 → 不再调用模型


async def test_force_redistill_keeps_approved_and_replaces_drafts(init_test_db, make_llm):
    main = make_content("xhs:main", "标题")
    llm = make_llm([
        _raw([_pair_dict(question="问题A？"), _pair_dict(question="问题B？")]),
        _raw([_pair_dict(question="新问题C？")]),
    ])
    svc = await _service(init_test_db, llm, [main], [make_analysis("aid-1", "xhs:main")])
    kb = SqliteKnowledgeBaseRepository()

    first = await svc.distill_from_analysis("aid-1")
    approved_id = first.pairs[0].qa_id
    await kb.update_qa_pairs_status([approved_id], "approved")

    again = await svc.distill_from_analysis("aid-1", force=True)

    assert again.replaced_drafts == 1          # 只删掉那 1 条草稿
    remain = await kb.list_qa_pairs(first.doc.doc_id)
    ids = {p.qa_id for p in remain}
    assert approved_id in ids                  # 人工审核成果保住了
    assert len(remain) == 2
    new = [p for p in remain if p.qa_id != approved_id][0]
    # 新 index 取"现存 pair 最大 index + 1"：被删 draft 腾出的号可以复用，绝不与现存撞号
    assert new.question == "新问题C？" and new.qa_index == 1
    assert again.doc.status == "pending"       # 新草稿未入库 → 需重新向量化


async def test_force_redistill_flags_reused_doc_back_to_pending(init_test_db, make_llm):
    main = make_content("xhs:main", "标题")
    llm = make_llm([_raw([_pair_dict()]), _raw([_pair_dict(question="重蒸？")])])
    svc = await _service(init_test_db, llm, [main], [make_analysis("aid-1", "xhs:main")])
    kb = SqliteKnowledgeBaseRepository()

    first = await svc.distill_from_analysis("aid-1")
    await kb.set_document_state(first.doc.doc_id, "ready", 3)

    again = await svc.distill_from_analysis("aid-1", force=True)
    assert again.doc.status == "pending" and again.doc.chunk_count == 3


async def test_zero_valid_pairs_raises_model_parse_error(init_test_db, make_llm):
    main = make_content("xhs:main", "标题")
    llm = make_llm([_raw([_pair_dict(question=""), _pair_dict(answer="")])])
    svc = await _service(init_test_db, llm, [main], [make_analysis("aid-1", "xhs:main")])

    with pytest.raises(ModelParseError):
        await svc.distill_from_analysis("aid-1")
    assert await SqliteKnowledgeBaseRepository().list_documents() == []  # 不落空文档


async def test_missing_llm_raises_503(init_test_db, make_llm):
    main = make_content("xhs:main", "标题")
    svc = await _service(init_test_db, None, [main], [make_analysis("aid-1", "xhs:main")])
    with pytest.raises(ModelNotConfiguredError):
        await svc.distill_from_analysis("aid-1")


async def test_unknown_analysis_raises_key_error(init_test_db, make_llm):
    svc = await _service(init_test_db, make_llm([]), [], [])
    with pytest.raises(KeyError):
        await svc.distill_from_analysis("不存在")


async def test_distill_from_content_without_analysis(init_test_db, make_llm):
    main = make_content("xhs:main", "裸作品")
    llm = make_llm([_raw([_pair_dict(evidence=[
        {"source_kind": "content", "ref": "xhs:main", "excerpt": "标题"},
        {"source_kind": "analysis", "ref": "aid-1", "excerpt": "无分析时不可引用"},
    ])])])
    svc = await _service(init_test_db, llm, [main], [])

    res = await svc.distill_from_content("xhs:main")
    assert res.doc.source_type == "content"
    assert [e["ref"] for e in res.pairs[0].evidence] == ["xhs:main"]
    assert res.dropped_refs == 1


async def test_distill_text_runs_clean_first(init_test_db, make_llm):
    llm = make_llm(["# 整理后的正文\n\n反差选材。", _raw([_pair_dict()])])
    svc = await _service(init_test_db, llm, [], [])

    res = await svc.distill_text("爆款笔记拆解", "宝子们 刚刚 这个笔记爆了")
    assert res.doc.source_type == "manual" and res.doc.doc_type == "qa"
    assert res.pairs[0].evidence == [] and res.pairs[0].source_id is None
    assert len(llm.calls) == 2                       # 先 clean（text）再蒸馏（json）
    assert "整理后的正文" in llm.calls[1][-1].content  # 蒸馏素材用的是清洗结果


async def test_distill_text_validates_input(init_test_db, make_llm):
    svc = await _service(init_test_db, make_llm([]), [], [])
    with pytest.raises(ValueError):
        await svc.distill_text("", "正文")
    with pytest.raises(ValueError):
        await svc.distill_text("标题", "   ")


async def test_manual_entry_works_without_llm(init_test_db):
    svc = await _service(init_test_db, None, [], [])
    res = await svc.create_manual_qa_document(
        "人工知识",
        [QaPairDraft(question="怎么起钩子？", answer="前 3 秒给反差。", tags=["钩子"])],
        tags=["钩子"],
    )
    assert res.doc.doc_type == "qa" and res.doc.status == "pending"
    assert res.pairs[0].status == "draft" and res.pairs[0].source_type == "manual"
    assert res.pairs[0].tags == ["钩子"] and res.pairs[0].qa_index == 0
    fresh = await SqliteKnowledgeBaseRepository().get_document(res.doc.doc_id)
    assert "怎么起钩子？" in fresh.markdown


async def test_manual_entry_rejects_empty(init_test_db):
    svc = await _service(init_test_db, None, [], [])
    with pytest.raises(ValueError):
        await svc.create_manual_qa_document("", [QaPairDraft(question="q", answer="a")])
    with pytest.raises(ValueError):
        await svc.create_manual_qa_document("标题", [])


def test_render_qa_markdown_covers_dimensions():
    from app.domain.models import KbQaPair

    p = KbQaPair(
        qa_id="1", doc_id="d", qa_index=0, question="Q？", answer="A。",
        dimensions={
            "technique": "夸张演绎", "persona": "素人", "hook": "反差开头", "structure": "三段",
            "transfer": ["换选题"], "transfer_risk": ["过度演绎"],
        },
        tags=["爆款拆解"],
    )
    md = render_qa_markdown("标题", [p])
    for token in ("# 标题", "## Q1：Q？", "A。", "表现手法", "IP 人设", "开头钩子", "结构节奏",
                  "迁移建议", "失败风险", "换选题", "过度演绎", "爆款拆解"):
        assert token in md, token
