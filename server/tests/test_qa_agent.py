"""RAG 问答测试：拒答三闸 + 引用校验 + 双后端等价（真 LC 与 shim 跑同一份链）。

FakeModelAdapter 复用 conftest 的（chat_json 按序吐 dict、记录 calls）；FakeEmbedding 是
确定性 token-presence 向量，与 test_qa_kb 同款。"""
from __future__ import annotations

import re

import pytest

from app.adapters.llm import ModelNotConfiguredError
from app.domain.models import KbDocument, KbQaPair
from app.repositories.kb import SqliteKnowledgeBaseRepository
from app.repositories.sqlite import SqliteAnalysisRepository, SqliteContentRepository
from app.services.knowledge_base import KnowledgeBaseService
from app.services.qa_agent import QaAgent

_CJK = r"[一-鿿]"


def _grams(text: str) -> list[str]:
    han = re.findall(_CJK, text)
    return han + [han[i] + han[i + 1] for i in range(len(han) - 1)] + re.findall(r"[A-Za-z0-9_]+", text)


class FakeEmbedding:
    def __init__(self):
        self._dim = 256
        self._vocab: dict[str, int] = {}

    def is_configured(self) -> bool:
        return True

    async def embed(self, items: list[dict]) -> list[list[float]]:
        out = []
        for it in items:
            vec = [0.0] * self._dim
            for tok in _grams(it.get("text") or ""):
                if tok not in self._vocab:
                    self._vocab[tok] = len(self._vocab) % self._dim
                vec[self._vocab[tok]] = 1.0
            out.append(vec)
        return out


async def _seed(init_test_db):
    """一个已入库的 Q&A chunk：问题+答案「夸张演绎 + 情绪反差」类知识。返回 (kb, emb, chunk_id)。"""
    repo = SqliteKnowledgeBaseRepository()
    emb = FakeEmbedding()
    kb = KnowledgeBaseService(repo, SqliteContentRepository(), SqliteAnalysisRepository(), emb, None)
    # 检索视图要求 analysis 型文档能回溯到 approved 源内容，否则被排除（默认拒绝）
    from app.domain.enums import ReviewStatus
    from app.domain.models import Analysis, Content

    content_repo = SqliteContentRepository()
    await content_repo.upsert(Content(
        content_id="xhs:src-1", platform="xhs", platform_item_id="src-1",
        review_status=ReviewStatus.APPROVED,
    ))
    await SqliteAnalysisRepository().add(Analysis(analysis_id="aid-1", content_id="xhs:src-1"))
    await repo.add_document(KbDocument(
        doc_id="d-qa", source_type="analysis", source_id="aid-1", doc_type="qa",
        title="爆款拆解", content_hash="h",
    ))
    await repo.add_qa_pairs([KbQaPair(
        qa_id="qa-1", doc_id="d-qa", qa_index=0,
        question="为什么夸张演绎能爆", answer="因为情绪反差强，前 3 秒给足冲突。",
        dimensions={"technique": "夸张演绎", "persona": "素人人设"},
        tags=["爆款拆解"], status="approved",
    )])
    await kb.vectorize_qa_document("d-qa")
    chunk_id = (await repo.list_chunks_by_doc("d-qa"))[0].chunk_id
    return kb, emb, chunk_id


def _hit(chunks):
    return chunks[0]


async def _ask(init_test_db, make_llm, llm_responses, query="为什么夸张演绎能爆", **agent_kwargs):
    kb, emb, chunk_id = await _seed(init_test_db)
    llm = make_llm(llm_responses)
    agent = QaAgent(kb, llm, top_k=4, min_score=0.25, **agent_kwargs)
    return agent, llm, await agent.ask(query), chunk_id


# ---- 拒答三闸 ----

async def test_gate1_empty_retrieval_no_llm_call(init_test_db, make_llm):
    kb = KnowledgeBaseService(
        SqliteKnowledgeBaseRepository(), SqliteContentRepository(), SqliteAnalysisRepository(),
        FakeEmbedding(), None,
    )
    llm = make_llm([])
    agent = QaAgent(kb, llm, top_k=4, min_score=0.25)

    ans = await agent.ask("知识库里不存在的话题")

    assert ans.answered is False and ans.reason == "empty_retrieval"
    assert ans.citations == [] and ans.retrieval_count == 0
    assert llm.calls == []  # 闸 1 不调模型


async def test_gate2_below_threshold_no_llm_call(init_test_db, make_llm):
    kb, _, _ = await _seed(init_test_db)
    llm = make_llm([])
    agent = QaAgent(kb, llm, top_k=4, min_score=0.999)  # 阈值压到不可能过

    ans = await agent.ask("完全无关的问题")
    assert ans.answered is False and ans.reason == "below_threshold"
    assert ans.top_score > 0.0 and ans.retrieval_count >= 1
    assert llm.calls == []  # 闸 2 不调模型


async def test_gate3_no_valid_citation_discards_answer(init_test_db, make_llm):
    kb, _, _ = await _seed(init_test_db)
    llm = make_llm([{
        "answer": "编造的答案",
        "citations": [{"chunk_id": "编造的-chunk", "quote": "不存在"}],
        "limitations": [],
    }])
    agent = QaAgent(kb, llm, top_k=4, min_score=0.25)

    ans = await agent.ask("为什么夸张演绎能爆")
    assert ans.answered is False and ans.reason == "no_valid_citation"
    assert ans.answer == "" and ans.citations == []  # 模型文本被丢弃


# ---- 正例 + 引用校验 ----

async def test_happy_path_with_verified_citation(init_test_db, make_llm):
    kb, _, chunk_id = await _seed(init_test_db)
    llm = make_llm([{
        "answer": "因为情绪反差强。",
        "citations": [{"chunk_id": chunk_id, "quote": "情绪反差强"}],
        "limitations": ["本地样本有限"],
    }])
    agent = QaAgent(kb, llm, top_k=4, min_score=0.25)

    ans = await agent.ask("为什么夸张演绎能爆")
    assert ans.answered is True
    assert ans.answer == "因为情绪反差强。"
    assert len(ans.citations) == 1
    c = ans.citations[0]
    assert c.verified is True               # quote 逐字对齐
    assert c.qa_id == "qa-1" and c.question == "为什么夸张演绎能爆"
    assert c.title == "爆款拆解" and c.doc_id == "d-qa"
    assert "本地样本有限" in ans.limitations
    # 检索上下文确实喂给了模型（含命中原文）
    user = llm.calls[0][-1].content
    assert "为什么夸张演绎能爆" in user and "情绪反差强" in user


async def test_fake_quote_not_verified_but_citation_valid(init_test_db, make_llm):
    kb, _, chunk_id = await _seed(init_test_db)
    llm = make_llm([{
        "answer": "答",
        "citations": [{"chunk_id": chunk_id, "quote": "这是编造改写的内容"}],
    }])
    ans = await (QaAgent(kb, llm, top_k=4, min_score=0.25)).ask("为什么夸张演绎能爆")
    assert ans.answered is True and len(ans.citations) == 1
    assert ans.citations[0].verified is False  # 引用 id 合法但 quote 对不上原文


async def test_out_of_scope_citation_dropped(init_test_db, make_llm):
    kb, _, chunk_id = await _seed(init_test_db)
    llm = make_llm([{
        "answer": "答",
        "citations": [
            {"chunk_id": chunk_id, "quote": "情绪反差强"},
            {"chunk_id": "别的-chunk", "quote": "越界"},
        ],
    }])
    ans = await (QaAgent(kb, llm, top_k=4, min_score=0.25)).ask("为什么夸张演绎能爆")
    assert len(ans.citations) == 1 and ans.citations[0].chunk_id == chunk_id


async def test_missing_llm_raises_503(init_test_db, make_llm):
    kb, _, _ = await _seed(init_test_db)
    agent = QaAgent(kb, None, top_k=4, min_score=0.25)
    with pytest.raises(ModelNotConfiguredError):
        await agent.ask("为什么夸张演绎能爆")


async def test_blank_query_raises(init_test_db, make_llm):
    kb, _, _ = await _seed(init_test_db)
    agent = QaAgent(kb, make_llm([]), top_k=4, min_score=0.25)
    with pytest.raises(ValueError):
        await agent.ask("   ")


# ---- 双后端等价：真 LC 与 shim 跑同一份链 ----

async def test_fallback_backend_same_result(init_test_db, make_llm, monkeypatch):
    import app.services.qa_agent as qa_mod
    from app.services.lc import _Lambda

    kb, _, chunk_id = await _seed(init_test_db)
    real = QaAgent(kb, make_llm([{
        "answer": "因为情绪反差强。",
        "citations": [{"chunk_id": chunk_id, "quote": "情绪反差强"}],
    }]), top_k=4, min_score=0.25)
    real_ans = await real.ask("为什么夸张演绎能爆")

    # 把 qa_agent 模块里已 import 的 RunnableLambda 换成 shim 的 _Lambda（等价 fallback）
    monkeypatch.setattr(qa_mod, "RunnableLambda", lambda fn: _Lambda(fn))
    fallback = QaAgent(kb, make_llm([{
        "answer": "因为情绪反差强。",
        "citations": [{"chunk_id": chunk_id, "quote": "情绪反差强"}],
    }]), top_k=4, min_score=0.25)
    fb_ans = await fallback.ask("为什么夸张演绎能爆")

    assert fb_ans.answered is real_ans.answered is True
    assert fb_ans.answer == real_ans.answer
    assert fb_ans.citations[0].chunk_id == real_ans.citations[0].chunk_id
    assert fb_ans.citations[0].verified is real_ans.citations[0].verified is True


async def test_fallback_backend_refusal_short_circuit(init_test_db, make_llm, monkeypatch):
    import app.services.qa_agent as qa_mod
    from app.services.lc import _Lambda

    kb, _, _ = await _seed(init_test_db)
    llm = make_llm([])
    monkeypatch.setattr(qa_mod, "RunnableLambda", lambda fn: _Lambda(fn))
    agent = QaAgent(kb, llm, top_k=4, min_score=0.999)

    ans = await agent.ask("无关问题")
    assert ans.reason == "below_threshold" and llm.calls == []
