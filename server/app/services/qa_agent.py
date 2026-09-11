"""RAG 问答服务：KbRetriever + LCEL 链 + 拒答三闸。

LangChain 仅作编排层（`langchain_core` 只经 app/services/lc.py 引入），链 = 提示 → 模型 → 守卫。
`RunnableLambda` 在真 LC 与 shim 两条后端行为等价，链只有一份实现。

拒答三闸（服务端权威、确定性、可测试）：
1. **前置短路**：检索为空 → 直接拒答，**不调用模型**（FakeLLM.calls==0 锁定）；
2. **阈值**：top_score < rag_min_score → 拒答并回传 top_score/retrieval_count 供校准；
3. **引用兜底**：模型 citations 与本次检索命中集求交，有效引用为 0 → 强制转拒答，
   原始答案文本丢弃不展示（红线：模型输出≠事实）。引用 quote 经空白归一做子串校验 → verified。
"""
from __future__ import annotations

import re
from typing import Any

from app.adapters.llm import ChatMessage, ModelAdapter, ModelNotConfiguredError
from app.schemas.api import KbAnswer, KbCitation
from app.services.knowledge_base import KnowledgeBaseService
from app.services.lc import RunnableLambda
from app.services.prompt_templates import build_rag_answer_messages

_WS = re.compile(r"\s+")


def _norm(text: str) -> str:
    return _WS.sub("", text or "").strip()


class KbRetriever:
    """检索薄封装：把 KB 服务命中的富字段（score/meta/qa_id）原样带进问答上下文。

    刻意不转成 LC 的 Document —— 引用校验需要 score/meta/qa_id/question，压进 Document.metadata
    只会徒增往返。检索结果用 dict 契约，LangChain 负责编排而非接管检索。
    """

    def __init__(self, kb_service: KnowledgeBaseService):
        self.kb_service = kb_service

    async def retrieve(
        self, query: str, top_k: int, app_visible_only: bool = False
    ) -> list[dict[str, Any]]:
        return await self.kb_service.search(
            query, top_k=top_k, app_visible_only=app_visible_only
        )


class QaAgent:
    """知识库问答编排：检索 → 三闸 → LCEL 链（提示/模型/守卫）。"""

    def __init__(
        self,
        kb_service: KnowledgeBaseService,
        llm: ModelAdapter | None,
        *,
        top_k: int = 6,
        min_score: float = 0.25,
        max_context_chars: int = 6000,
    ):
        self.retriever = KbRetriever(kb_service)
        self.llm = llm
        self.top_k = top_k
        self.min_score = min_score
        self.max_context_chars = max_context_chars
        # 链只建一次；各步骤引用 self 的绑定方法，调用时取最新状态
        self._chain = (
            RunnableLambda(self._prompt_step)
            | RunnableLambda(self._model_step)
            | RunnableLambda(self._guard_step)
        )

    async def ask(
        self, query: str, top_k: int | None = None,
        history: list[dict[str, str]] | None = None,
        app_visible_only: bool = False,
    ) -> KbAnswer:
        """`app_visible_only` 只作用于检索。**引用守卫不需要跟着改**：命中集里没有
        被隐藏内容的 chunk，模型若仍写进 citations 就对不齐命中集，会被既有的
        「有效引用为 0 → 强制拒答并丢弃模型文本」拦下。复用红线比另造一套更安全。
        """
        if self.llm is None or not self.llm.is_configured():
            raise ModelNotConfiguredError("服务端未配置模型 API key，无法进行知识库问答")
        query = (query or "").strip()
        if not query:
            raise ValueError("问题不能为空")

        hits = await self.retriever.retrieve(
            query, max(top_k or self.top_k, 1), app_visible_only=app_visible_only
        )
        retrieval_count = len(hits)
        top_score = round(float(hits[0]["score"]) if hits else 0.0, 4)

        # 闸 1：检索为空 → 不调模型
        if not hits:
            return KbAnswer(
                answered=False, reason="empty_retrieval",
                top_score=0.0, retrieval_count=0,
                limitations=["知识库中没有与问题相关的内容"],
            )
        # 闸 2：最高相似度低于阈值 → 不调模型
        if top_score < self.min_score:
            return KbAnswer(
                answered=False, reason="below_threshold",
                top_score=top_score, retrieval_count=retrieval_count,
                limitations=[f"最相关命中相似度 {top_score} 低于阈值 {self.min_score}"],
            )

        context = {
            "query": query, "hits": hits,
            "top_score": top_score, "retrieval_count": retrieval_count,
            "history": [
                {"query": str(item.get("query", ""))[:500], "answer": str(item.get("answer", ""))[:1500]}
                for item in (history or [])
                if isinstance(item, dict) and str(item.get("query", "")).strip()
            ][-6:],
        }
        return await self._chain.ainvoke(context)

    # ---- 链的三步 ----

    async def _prompt_step(self, context: dict) -> dict:
        messages = build_rag_answer_messages(context, self.max_context_chars)
        history = context.get("history") or []
        if history:
            memory = "【历史会话（仅用于理解上下文，事实必须以本次检索结果为准）】\n" + "\n".join(
                f"Q：{item['query']}\nA：{item['answer']}" for item in history
            )
            messages.append(ChatMessage(role="user", content=memory))
        return {
            "messages": messages,
            "context": context,
        }

    async def _model_step(self, pack: dict) -> dict:
        raw = await self.llm.chat_json(pack["messages"])
        return {"raw": raw, "context": pack["context"]}

    def _guard_step(self, pack: dict) -> KbAnswer:
        return self._finalize(pack["raw"], pack["context"])

    # ---- 守卫：引用求交 + quote 校验 ----

    def _finalize(self, raw: dict, context: dict) -> KbAnswer:
        hits = context["hits"]
        by_chunk = {h["chunk_id"]: h for h in hits}
        answer = raw.get("answer") if isinstance(raw, dict) else ""
        answer = answer if isinstance(answer, str) else ""
        raw_citations = raw.get("citations") if isinstance(raw, dict) else None
        raw_citations = raw_citations if isinstance(raw_citations, list) else []

        valid: list[KbCitation] = []
        seen: set[str] = set()
        for c in raw_citations:
            if not isinstance(c, dict):
                continue
            cid = c.get("chunk_id")
            if not isinstance(cid, str) or cid not in by_chunk or cid in seen:
                continue  # 越界引用 / 重复引用丢弃
            seen.add(cid)
            hit = by_chunk[cid]
            quote = c.get("quote") if isinstance(c.get("quote"), str) else ""
            verified = bool(quote) and _norm(quote) in _norm(hit["text"])
            meta = hit.get("meta") or {}
            valid.append(
                KbCitation(
                    chunk_id=cid, doc_id=hit["doc_id"], title=meta.get("title") or "",
                    text=hit["text"], score=hit["score"], verified=verified,
                    qa_id=meta.get("qa_id"), question=meta.get("question"),
                )
            )

        # 闸 3：有效引用为 0 → 强制拒答，模型文本丢弃
        if not valid:
            return KbAnswer(
                answered=False, reason="no_valid_citation", answer="", citations=[],
                top_score=round(float(context["top_score"]), 4),
                retrieval_count=context["retrieval_count"],
                limitations=["模型未给出可对齐到检索结果的引用，答案已丢弃"],
            )

        limitations = [x for x in (raw.get("limitations") or []) if isinstance(x, str)][:10]
        return KbAnswer(
            answered=True, answer=answer, citations=valid,
            top_score=round(float(context["top_score"]), 4),
            retrieval_count=context["retrieval_count"],
            limitations=limitations,
        )
