"""Q&A 知识库测试：审核闸门 / 每对一 chunk / 检索兼容（qa_boost=0 与旧行为一致）/ 计数富化。"""
from __future__ import annotations

import re

import pytest

from app.adapters.embedding import EmbeddingNotConfiguredError, EmbeddingProviderError
from app.domain.models import KbQaPair
from app.repositories.kb import SqliteKnowledgeBaseRepository
from app.repositories.sqlite import SqliteAnalysisRepository, SqliteContentRepository
from app.services.knowledge_base import KnowledgeBaseService

_CJK = r"[一-鿿]"


def _grams(text: str) -> list[str]:
    han = re.findall(_CJK, text)
    return han + [han[i] + han[i + 1] for i in range(len(han) - 1)] + re.findall(r"[A-Za-z0-9_]+", text)


class FakeEmbedding:
    """确定性 token-presence 向量：同文本 cosine=1，共现词得分高。"""

    def __init__(self, *, configured: bool = True, fail_all: bool = False):
        self.configured = configured
        self.fail_all = fail_all
        self._dim = 256
        self._vocab: dict[str, int] = {}
        self.calls = 0

    def is_configured(self) -> bool:
        return self.configured

    async def embed(self, items: list[dict]) -> list[list[float]]:
        self.calls += 1
        if not self.configured:
            raise EmbeddingNotConfiguredError("no key")
        if self.fail_all:
            raise EmbeddingProviderError("provider boom")
        out = []
        for it in items:
            vec = [0.0] * self._dim
            for tok in _grams(it.get("text") or ""):
                if tok not in self._vocab:
                    self._vocab[tok] = len(self._vocab) % self._dim
                vec[self._vocab[tok]] = 1.0
            out.append(vec)
        return out


def _service(embedder=None, qa_boost: float = 0.0) -> KnowledgeBaseService:
    return KnowledgeBaseService(
        kb_repo=SqliteKnowledgeBaseRepository(),
        content_repo=SqliteContentRepository(),
        analysis_repo=SqliteAnalysisRepository(),
        embedder=embedder if embedder is not None else FakeEmbedding(),
        llm=None,
        qa_boost=qa_boost,
    )


async def _seed_analysis_chain(analysis_id: str, content_id: str) -> None:
    """造出 doc.source_id 能真实回溯到 approved 源内容的链路。

    检索视图对 analysis 型文档要求 JOIN 得到 analyses 且其源内容 review_status=approved；
    悬空 source_id 会被判为「审核状态未知」而排除（默认拒绝，见 iter_retrievable_chunks）。
    """
    from app.domain.enums import ReviewStatus
    from app.domain.models import Analysis, Content

    await SqliteContentRepository().upsert(Content(
        content_id=content_id, platform="xhs", platform_item_id=content_id.split(":")[-1],
        review_status=ReviewStatus.APPROVED,
    ))
    await SqliteAnalysisRepository().add(Analysis(analysis_id=analysis_id, content_id=content_id))


async def _seed_qa_doc(doc_id="doc-qa", pairs=None, status="draft", embedder=None) -> str:
    repo = SqliteKnowledgeBaseRepository()
    from app.domain.models import KbDocument

    await _seed_analysis_chain("aid-1", "xhs:src-1")
    await repo.add_document(
        KbDocument(doc_id=doc_id, source_type="analysis", source_id="aid-1", doc_type="qa",
                   title="爆款拆解", content_hash="h")
    )
    rows = pairs or [
        KbQaPair(
            qa_id=f"{doc_id}-p{i}", doc_id=doc_id, qa_index=i, question=f"问题{i}？",
            answer=f"答案{i}", dimensions={"technique": "夸张演绎", "transfer": ["换选题"]},
            tags=["爆款拆解"], status=status,
        )
        for i in range(3)
    ]
    await repo.add_qa_pairs(rows)
    return doc_id


# ---- 审核闸门 + 每对一 chunk ----

async def test_vectorize_only_approved_pairs(init_test_db):
    doc_id = await _seed_qa_doc()
    kb = SqliteKnowledgeBaseRepository()
    await kb.update_qa_pairs_status(["doc-qa-p0", "doc-qa-p1"], "approved")
    await kb.update_qa_pairs_status(["doc-qa-p2"], "rejected")
    svc = _service()

    doc = await svc.vectorize_qa_document(doc_id)

    assert doc.status == "ready" and doc.chunk_count == 2  # draft/rejected 永不入库
    chunks = await kb.list_chunks_by_doc(doc_id)
    assert len(chunks) == 2
    c = chunks[0]
    assert "问题0？" in c.text and "答案0" in c.text      # 问题与答案同块，绝不拆散
    assert "迁移建议：换选题" in c.text
    assert c.meta["kind"] == "qa" and c.meta["qa_id"] == "doc-qa-p0"
    assert c.meta["question"] == "问题0？" and c.meta["chunk_total"] == 2
    assert c.meta["source_type"] == "analysis" and c.meta["analysis_id"] == "aid-1"


async def test_vectorize_without_approved_raises(init_test_db):
    doc_id = await _seed_qa_doc()  # 全是 draft
    with pytest.raises(ValueError):
        await _service().vectorize_qa_document(doc_id)
    assert await SqliteKnowledgeBaseRepository().list_chunks_by_doc(doc_id) == []


async def test_vectorize_document_routes_qa_docs(init_test_db):
    doc_id = await _seed_qa_doc()
    await SqliteKnowledgeBaseRepository().update_qa_pairs_status(["doc-qa-p0"], "approved")
    doc = await _service().vectorize_document(doc_id)  # 前端既有按钮
    assert doc.status == "ready" and doc.chunk_count == 1


async def test_vectorize_qa_rejects_general_doc(init_test_db):
    from app.domain.models import KbDocument

    kb = SqliteKnowledgeBaseRepository()
    await kb.add_document(KbDocument(doc_id="d-gen", title="普通", content_hash="h"))
    with pytest.raises(ValueError):
        await _service().vectorize_qa_document("d-gen")


async def test_ready_qa_doc_is_short_circuited(init_test_db):
    doc_id = await _seed_qa_doc()
    kb = SqliteKnowledgeBaseRepository()
    await kb.update_qa_pairs_status(["doc-qa-p0"], "approved")
    svc = _service()
    await svc.vectorize_qa_document(doc_id)
    calls_before = svc.embedder.calls

    again = await svc.vectorize_qa_document(doc_id)
    assert again.status == "ready" and svc.embedder.calls == calls_before  # 无变动不重复花钱


async def test_embedding_failure_keeps_old_chunks(init_test_db):
    doc_id = await _seed_qa_doc()
    kb = SqliteKnowledgeBaseRepository()
    await kb.update_qa_pairs_status(["doc-qa-p0"], "approved")
    good = _service()
    await good.vectorize_qa_document(doc_id)
    assert len(await kb.list_chunks_by_doc(doc_id)) == 1

    await kb.set_document_state(doc_id, "pending", 1)  # 模拟变动后重向量化
    boom = _service(FakeEmbedding(fail_all=True))
    with pytest.raises(EmbeddingProviderError):
        await boom.vectorize_qa_document(doc_id)

    assert len(await kb.list_chunks_by_doc(doc_id)) == 1  # 旧知识没被删掉
    assert (await kb.get_document(doc_id)).status == "failed"


async def test_no_embedder_raises_503(init_test_db):
    doc_id = await _seed_qa_doc()
    await SqliteKnowledgeBaseRepository().update_qa_pairs_status(["doc-qa-p0"], "approved")
    svc = _service(FakeEmbedding(configured=False))
    with pytest.raises(EmbeddingNotConfiguredError):
        await svc.vectorize_qa_document(doc_id)


# ---- 审核/编辑：改动必须回落待向量化 ----

async def test_review_qa_pairs_counts_and_status(init_test_db):
    await _seed_qa_doc()
    svc = _service()
    out = await svc.review_qa_pairs(["doc-qa-p0", "doc-qa-p1", "不存在"], "approved")
    assert out == {"changed": 2, "requested": 3}
    with pytest.raises(ValueError):
        await svc.review_qa_pairs(["doc-qa-p0"], "乱写")


async def test_editing_approved_pair_falls_back_to_draft(init_test_db):
    doc_id = await _seed_qa_doc()
    kb = SqliteKnowledgeBaseRepository()
    await kb.update_qa_pairs_status(["doc-qa-p0"], "approved")
    await kb.set_document_state(doc_id, "ready", 1)
    svc = _service()

    updated = await svc.update_qa_pair("doc-qa-p0", answer="改写后的答案")

    assert updated.status == "draft" and updated.answer == "改写后的答案"
    assert (await kb.get_document(doc_id)).status == "pending"  # 需重新审核 + 向量化


async def test_editing_draft_pair_touches_ready_doc(init_test_db):
    doc_id = await _seed_qa_doc()
    kb = SqliteKnowledgeBaseRepository()
    await kb.set_document_state(doc_id, "ready", 2)
    svc = _service()

    pair = await svc.update_qa_pair("doc-qa-p1", question="换个问法？", tags=["新标签"])
    assert pair.status == "draft" and pair.question == "换个问法？" and pair.tags == ["新标签"]
    assert (await kb.get_document(doc_id)).status == "pending"


async def test_editing_with_no_change_is_noop(init_test_db):
    doc_id = await _seed_qa_doc()
    kb = SqliteKnowledgeBaseRepository()
    await kb.set_document_state(doc_id, "ready", 1)
    before = await kb.get_qa_pair("doc-qa-p0")

    same = await _service().update_qa_pair("doc-qa-p0", question=before.question)
    assert same.updated_at == before.updated_at
    assert (await kb.get_document(doc_id)).status == "ready"  # 无实质改动不打断 ready


async def test_update_unknown_pair_raises(init_test_db):
    with pytest.raises(KeyError):
        await _service().update_qa_pair("不存在", answer="x")


async def test_delete_pair_touches_doc(init_test_db):
    doc_id = await _seed_qa_doc()
    kb = SqliteKnowledgeBaseRepository()
    await kb.set_document_state(doc_id, "ready", 3)
    await _service().delete_qa_pair("doc-qa-p1")

    assert [p.qa_id for p in await kb.list_qa_pairs(doc_id)] == ["doc-qa-p0", "doc-qa-p2"]
    assert (await kb.get_document(doc_id)).status == "pending"
    with pytest.raises(KeyError):
        await _service().delete_qa_pair("doc-qa-p1")


# ---- 检索兼容 ----

async def _seed_general_doc(doc_id: str, text: str, embedder: FakeEmbedding) -> None:
    """普通 markdown 文档的一个 chunk。必须与检索用同一个 embedder 实例（词表在实例内）。"""
    import uuid

    from app.domain.models import KbChunk, KbDocument

    repo = SqliteKnowledgeBaseRepository()
    await repo.add_document(
        KbDocument(doc_id=doc_id, source_type="manual", title=text[:10], content_hash=f"h-{doc_id}")
    )
    vec = (await embedder.embed([{"text": text}]))[0]
    await repo.add_chunks([
        KbChunk(chunk_id=uuid.uuid4().hex, doc_id=doc_id, chunk_index=0,
                text=text, embedding=vec, meta={"kind": "doc"})
    ])


async def _seed_pair(emb: FakeEmbedding) -> None:
    """同一 embedder 实例下：一个已入库的 Q&A chunk + 一个同文本普通 chunk（同分）。"""
    doc_id = await _seed_qa_doc(pairs=[KbQaPair(
        qa_id="q1", doc_id="doc-qa", qa_index=0,
        question="为什么夸张演绎能爆", answer="因为情绪反差强", status="approved",
    )])
    await _service(emb).vectorize_qa_document(doc_id)
    await _seed_general_doc("d-gen", "为什么夸张演绎能爆", emb)


async def test_search_hits_both_qa_and_general_zero_boost(init_test_db):
    emb = FakeEmbedding()
    await _seed_pair(emb)

    hits = await _service(emb).search("为什么夸张演绎能爆", top_k=5)

    assert len(hits) == 2
    for h in hits:
        assert h["rank_score"] == h["score"]  # boost 关闭：加权分 = 原始余弦，排序未受影响
    assert {h["meta"].get("kind") for h in hits} == {"qa", "doc"}  # 两类同批命中
    assert all(h["score"] > 0.0 for h in hits)


async def test_qa_boost_promotes_qa_chunk(init_test_db):
    emb = FakeEmbedding()
    await _seed_pair(emb)

    plain = await _service(emb, qa_boost=0.0).search("为什么夸张演绎能爆", top_k=5)
    boosted = await _service(emb, qa_boost=0.5).search("为什么夸张演绎能爆", top_k=5)

    assert {h["chunk_id"] for h in plain} == {h["chunk_id"] for h in boosted}
    assert boosted[0]["meta"]["kind"] == "qa"          # Q&A 被抬到最前
    assert boosted[0]["rank_score"] > boosted[0]["score"]
    assert all(h["score"] <= 1.0 for h in boosted)     # score 仍是原始余弦，语义不变


async def test_zero_boost_order_matches_pure_score_ranking(init_test_db):
    emb = FakeEmbedding()
    await _seed_general_doc("d-a", "苹果 香蕉 橘子 西瓜", emb)
    await _seed_general_doc("d-b", "苹果 香蕉 橘子", emb)

    hits = await _service(emb, qa_boost=0.0).search("苹果 香蕉 橘子 西瓜", top_k=5)
    scores = [h["score"] for h in hits]
    assert scores == sorted(scores, reverse=True)  # 关闭 boost → 严格按原始余弦降序
    assert hits[0]["doc_id"] == "d-a"


async def test_search_request_override_beats_config(init_test_db):
    emb = FakeEmbedding()
    await _seed_pair(emb)

    forced = await _service(emb, qa_boost=0.0).search(
        "为什么夸张演绎能爆", top_k=5, qa_boost=0.9
    )
    assert forced[0]["meta"]["kind"] == "qa"


# ---- 列表富化 ----

async def test_fill_qa_counts(init_test_db):
    doc_id = await _seed_qa_doc()
    kb = SqliteKnowledgeBaseRepository()
    await kb.update_qa_pairs_status(["doc-qa-p0", "doc-qa-p1"], "approved")
    await kb.update_qa_pairs_status(["doc-qa-p2"], "rejected")
    from app.domain.models import KbDocument

    await kb.add_document(KbDocument(doc_id="d-gen", title="普通", content_hash="h2"))
    svc = _service()

    docs = await svc.fill_qa_counts(await kb.list_documents())
    by_id = {d.doc_id: d for d in docs}
    assert (by_id["doc-qa"].qa_pair_count, by_id["doc-qa"].qa_approved_count) == (3, 2)
    assert (by_id["d-gen"].qa_pair_count, by_id["d-gen"].qa_approved_count) == (0, 0)


async def test_count_qa_pairs_by_status(init_test_db):
    await _seed_qa_doc()
    kb = SqliteKnowledgeBaseRepository()
    await kb.update_qa_pairs_status(["doc-qa-p0"], "approved")
    assert await kb.count_qa_pairs_by_doc(["doc-qa"]) == {"doc-qa": 3}
    assert await kb.count_qa_pairs_by_doc(["doc-qa"], status="approved") == {"doc-qa": 1}
    assert await kb.count_qa_pairs_by_doc(["doc-qa"], status="rejected") == {}
    assert await kb.count_qa_pairs_by_doc([]) == {}
