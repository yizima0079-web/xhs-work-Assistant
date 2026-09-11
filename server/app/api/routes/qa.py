"""Q&A 知识 API：AI 蒸馏 / 手动录入 / 人工审核 / 问答。

双通道来源（决策）：
- **AI 蒸馏**：作品分析或作品本身 → 一键产出问答草稿（走人工审核）；
- **手动录入**：人结构化撰写，question/answer 即终态 —— **不走 LLM 清洗，无 key 也能用**
  （红线由 draft→approved 的人工闸门满足，而不是靠模型）。
两通道产出的草稿都必须 approve 后才被向量化进库；未审核内容永不进入检索。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.routes.knowledge_base import _kb_or_error
from app.auth import app_channel
from app.domain.models import KbQaPair
from app.schemas.api import (
    KbAnswer,
    KbAskRequest,
    KbQaDistillTextRequest,
    KbQaIdsRequest,
    KbQaManualRequest,
    KbQaPairInput,
    KbQaReviewResult,
    KbQaUpdateRequest,
)
from app.schemas.qa import DistillResult, QaEvidence, QaPairDraft

router = APIRouter()


async def _with_counts(request: Request, result: DistillResult) -> DistillResult:
    """与 list/get/vectorize 一致：DistillResult.doc 回填问答对数。

    面板刚录入/蒸馏完就渲染文档行，若此处为 0 会与随后 GET /kb/documents 的值冲突。
    """
    kb = request.app.state.kb_service
    result.doc = (await kb.fill_qa_counts([result.doc]))[0]
    return result


@router.post("/api/v1/kb/qa/documents", response_model=DistillResult)
async def create_manual_qa(body: KbQaManualRequest, request: Request) -> DistillResult:
    """手动录入问答知识（结构化终态，不调用模型）。"""
    svc = request.app.state.qa_distiller
    pairs = [
        QaPairDraft(
            question=p.question, answer=p.answer, dimensions=p.dimensions,
            evidence=[QaEvidence(**e) for e in p.evidence if isinstance(e, dict)],
            tags=p.tags,
        )
        for p in body.pairs
    ]
    return await _with_counts(
        request,
        await _kb_or_error(
            svc, svc.create_manual_qa_document(body.title, pairs, body.tags, body.url)
        ),
    )


@router.post("/api/v1/kb/qa/distill", response_model=DistillResult)
async def distill_text(body: KbQaDistillTextRequest, request: Request) -> DistillResult:
    """把一段人工粘贴的爆款拆解文本蒸馏成问答草稿（先规则清洗+LLM 整理，再蒸馏）。"""
    svc = request.app.state.qa_distiller
    return await _with_counts(
        request, await _kb_or_error(svc, svc.distill_text(body.title, body.text, body.tags))
    )


@router.post("/api/v1/kb/analyses/{analysis_id}/distill-qa", response_model=DistillResult)
async def distill_from_analysis(
    analysis_id: str, request: Request, force: bool = False
) -> DistillResult:
    """作品分析 → Q&A 草稿。同源已存在时默认复用；force=true 重蒸馏（只清旧草稿）。"""
    svc = request.app.state.qa_distiller
    return await _with_counts(
        request,
        await _kb_or_error(svc, svc.distill_from_analysis(analysis_id, force=force)),
    )


@router.post("/api/v1/kb/contents/{content_id}/distill-qa", response_model=DistillResult)
async def distill_from_content(
    content_id: str, request: Request, force: bool = False
) -> DistillResult:
    """作品本身（含最近一次分析，若有）→ Q&A 草稿。"""
    svc = request.app.state.qa_distiller
    return await _with_counts(
        request,
        await _kb_or_error(svc, svc.distill_from_content(content_id, force=force)),
    )


@router.get("/api/v1/kb/qa/documents/{doc_id}/pairs", response_model=list[KbQaPair])
async def list_qa_pairs(doc_id: str, request: Request) -> list[KbQaPair]:
    """app 通道下，源内容被隐藏的文档其问答对整批不返回（判定依据是 doc 的可见性）。"""
    kb = request.app.state.kb_service
    return await _kb_or_error(
        kb, kb.list_qa_pairs(doc_id, app_visible_only=app_channel(request))
    )


@router.patch("/api/v1/kb/qa/pairs/{qa_id}", response_model=KbQaPair)
async def update_qa_pair(qa_id: str, body: KbQaUpdateRequest, request: Request) -> KbQaPair:
    """编辑问答对；已审核内容被改写会自动回落草稿，需重新审核并向量化。"""
    kb = request.app.state.kb_service
    return await _kb_or_error(
        kb,
        kb.update_qa_pair(
            qa_id,
            question=body.question,
            answer=body.answer,
            dimensions=body.dimensions,
            tags=body.tags,
        ),
    )


@router.post("/api/v1/kb/qa/pairs/approve", response_model=KbQaReviewResult)
async def approve_qa_pairs(body: KbQaIdsRequest, request: Request) -> KbQaReviewResult:
    kb = request.app.state.kb_service
    out = await _kb_or_error(kb, kb.review_qa_pairs(body.qa_ids, "approved"))
    return KbQaReviewResult(**out)


@router.post("/api/v1/kb/qa/pairs/reject", response_model=KbQaReviewResult)
async def reject_qa_pairs(body: KbQaIdsRequest, request: Request) -> KbQaReviewResult:
    kb = request.app.state.kb_service
    out = await _kb_or_error(kb, kb.review_qa_pairs(body.qa_ids, "rejected"))
    return KbQaReviewResult(**out)


@router.delete("/api/v1/kb/qa/pairs/{qa_id}", status_code=204)
async def delete_qa_pair(qa_id: str, request: Request) -> None:
    kb = request.app.state.kb_service
    await _kb_or_error(kb, kb.delete_qa_pair(qa_id))


@router.post("/api/v1/kb/ask", response_model=KbAnswer)
async def ask_kb(body: KbAskRequest, request: Request) -> KbAnswer:
    """基于知识库问答：带引用、无据拒答（检索空 / 低于阈值 / 无有效引用三闸）。

    app 通道下被隐藏内容的 chunk 不进检索 —— 否则会出现「内容已删掉、答案里还在
    引用它」的漏。守卫不用额外改：命中集里没有它，模型引用就对不齐，会走既有的
    拒答红线。
    """
    agent = request.app.state.qa_agent
    return await _kb_or_error(
        agent,
        agent.ask(
            body.query,
            top_k=body.top_k,
            history=body.history,
            app_visible_only=app_channel(request),
        ),
    )
