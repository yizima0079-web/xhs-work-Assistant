"""审核 API：触发自动审核 / 查内容审核详情 / 查单条断言 / 人工 decision。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.adapters.llm import (
    ModelError,
    ModelNotConfiguredError,
    ModelParseError,
    ModelProviderError,
)
from app.auth import app_channel
from app.domain.models import Claim
from app.schemas.api import (
    ClaimSnapshot,
    ContentReviewDetail,
    DecisionRequest,
    ReviewBatch,
    SubmitReviewResponse,
)

router = APIRouter()


def _model_status(exc: ModelError) -> int:
    """模型层错误 → HTTP：未配置/解析失败=503，provider 不可达=502。"""
    if isinstance(exc, ModelProviderError):
        return 502
    return 503


@router.post("/api/v1/contents/{content_id}/review", response_model=SubmitReviewResponse)
async def submit_review(
    content_id: str, request: Request, force: bool = False
) -> SubmitReviewResponse:
    """自动审核。?force=true 为「重新审核」：忽略既有状态，**新增一个审核批次**。

    旧批次原样保留（不覆盖、不删除），因此重审失败也不会毁掉上一版结果。
    """
    service = request.app.state.review_service
    try:
        return await service.review_content(
            content_id, force=force, app_visible_only=app_channel(request)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:  # 非 pending（已审核）→ 409，要重审须显式 force
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ModelError as exc:
        raise HTTPException(status_code=_model_status(exc), detail=str(exc)) from exc


@router.get("/api/v1/contents/{content_id}/claims", response_model=ContentReviewDetail)
async def get_content_review(
    content_id: str, request: Request, batch_id: str | None = None
) -> ContentReviewDetail:
    """内容审核详情。?batch_id= 指定批次，不传则返回生效批次（并回带全部历史批次）。

    app 通道下，被隐藏的内容在这里**等同于不存在** → 404（断言与证据是内容的
    拆解，内容看不到就不该看到它们）。
    """
    service = request.app.state.review_service
    try:
        return await service.get_content_review(
            content_id, batch_id=batch_id, app_visible_only=app_channel(request)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/api/v1/contents/{content_id}/review-batches", response_model=list[ReviewBatch])
async def list_review_batches(content_id: str, request: Request) -> list[ReviewBatch]:
    service = request.app.state.review_service
    try:
        return await service.list_review_batches(
            content_id, app_visible_only=app_channel(request)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/api/v1/contents/{content_id}/review-batches/{batch_id}/activate",
    response_model=SubmitReviewResponse,
)
async def activate_review_batch(
    content_id: str, batch_id: str, request: Request
) -> SubmitReviewResponse:
    """把历史批次切为生效版本，并按该批次断言重算内容状态。"""
    service = request.app.state.review_service
    try:
        status = await service.activate_review_batch(content_id, batch_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    claims = await request.app.state.review_repo.list_claims(content_id, batch_id)
    return SubmitReviewResponse(
        content_id=content_id, review_status=status, claim_count=len(claims)
    )


@router.get("/api/v1/claims/{claim_id}", response_model=ClaimSnapshot)
async def get_claim(claim_id: str, request: Request) -> ClaimSnapshot:
    """app 通道下，源内容被隐藏的断言返回 404 —— 断言与证据是内容的拆解。"""
    review_repo = request.app.state.review_repo
    claim = await review_repo.get_claim(claim_id, app_visible_only=app_channel(request))
    if claim is None:
        raise HTTPException(status_code=404, detail=f"断言不存在: {claim_id}")
    return ClaimSnapshot(
        claim=claim,
        evidence=await review_repo.list_evidence(
            claim_id, app_visible_only=app_channel(request)
        ),
        decisions=await review_repo.list_decisions(claim_id),
    )


@router.post("/api/v1/claims/{claim_id}/decision", response_model=SubmitReviewResponse)
async def human_decision(claim_id: str, body: DecisionRequest, request: Request) -> SubmitReviewResponse:
    review_repo = request.app.state.review_repo
    claim: Claim | None = await review_repo.get_claim(claim_id)
    if claim is None:
        raise HTTPException(status_code=404, detail=f"断言不存在: {claim_id}")
    service = request.app.state.review_service
    try:
        content_status = await service.human_decide(
            claim_id, body.status, rationale=body.rationale, evidence_ids=body.evidence_ids
        )
    except ValueError as exc:  # evidence 引用越界
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # 计数与状态都限定在该 claim 所属批次内，避免把其他版本的断言算进来
    siblings = await review_repo.list_claims(claim.content_id, claim.batch_id)
    return SubmitReviewResponse(
        content_id=claim.content_id, review_status=content_status, claim_count=len(siblings)
    )
