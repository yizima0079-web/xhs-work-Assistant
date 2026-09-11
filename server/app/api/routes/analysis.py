"""作品分析 API：单条内容生成爆款/平淡归因分析 / 查询。"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException, Request

from app.adapters.llm import ModelError, ModelProviderError
from app.auth import app_channel
from app.domain.models import Analysis
from app.schemas.api import AnalyzeRequest

router = APIRouter()


def _status(exc: ModelError) -> int:
    return 502 if isinstance(exc, ModelProviderError) else 503


@router.post("/api/v1/contents/{content_id}/analyze", response_model=Analysis)
async def analyze_content(
    content_id: str,
    request: Request,
    body: AnalyzeRequest | None = Body(default=None),
) -> Analysis:
    """标准作品分析；body.focus 非空时在既有框架内附加用户指定的观察侧重。

    body 可省略（旧客户端不带 body 即为标准分析）。每次调用**新增一条**分析记录，
    旧记录保留 → 「查看分析」看历史、「重新分析」出新版本。
    """
    service = request.app.state.analysis_service
    try:
        return await service.analyze(
            content_id,
            focus=(body.focus if body else ""),
            app_visible_only=app_channel(request),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ModelError as exc:
        raise HTTPException(status_code=_status(exc), detail=str(exc)) from exc


@router.get("/api/v1/analyses/{analysis_id}", response_model=Analysis)
async def get_analysis(analysis_id: str, request: Request) -> Analysis:
    """app 通道下，源内容被隐藏的分析返回 404（不是 403）—— 否则等于告诉 app
    「这条分析存在、只是被隐藏了」。分析 payload 里是整篇内容的拆解，必须挡。"""
    analysis = await request.app.state.analysis_service.get(
        analysis_id, app_visible_only=app_channel(request)
    )
    if analysis is None:
        raise HTTPException(status_code=404, detail=f"分析不存在: {analysis_id}")
    await _mark_in_kb(request, [analysis])
    return analysis


@router.get("/api/v1/contents/{content_id}/analyses", response_model=list[Analysis])
async def list_content_analyses(
    content_id: str, request: Request, limit: int = 50, offset: int = 0
) -> list[Analysis]:
    items = await request.app.state.analysis_service.list_by_content(
        content_id, limit=limit, offset=offset, app_visible_only=app_channel(request)
    )
    await _mark_in_kb(request, items)
    return items


async def _mark_in_kb(request: Request, items: list[Analysis]) -> None:
    """给分析列表打「是否已入库」标记：多版本并存时用户要看清哪一版已在库。

    按 analysis_id 批量查一次，避免逐条 get_document_by_source 的 N+1。
    """
    if not items:
        return
    ready = await request.app.state.kb_repo.get_ready_source_ids(
        "analysis", [a.analysis_id for a in items]
    )
    for a in items:
        a.in_kb = a.analysis_id in ready
