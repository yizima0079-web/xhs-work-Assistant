"""报告 API：跨内容生成 §9.1 结构化报告（JSON + Markdown）/ 查询。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.adapters.llm import ModelError, ModelProviderError
from app.auth import app_channel
from app.domain.models import Report
from app.schemas.api import ReportCreateRequest

router = APIRouter()


def _status(exc: ModelError) -> int:
    return 502 if isinstance(exc, ModelProviderError) else 503


@router.post("/api/v1/reports", response_model=Report)
async def create_report(body: ReportCreateRequest, request: Request) -> Report:
    service = request.app.state.report_service
    try:
        return await service.build_report(
            body.content_ids, title=body.title, app_visible_only=app_channel(request)
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ModelError as exc:
        raise HTTPException(status_code=_status(exc), detail=str(exc)) from exc


@router.get("/api/v1/reports", response_model=list[Report])
async def list_reports(
    request: Request, limit: int = 50, offset: int = 0, content_id: str | None = None
) -> list[Report]:
    """content_id 非空 → 只返回包含该内容的报告（单篇报告的「查看历史」）。

    app 通道下，引用了**任一**被隐藏内容的报告整篇不返回 —— 报告里逐字嵌了每条
    内容的标题与正文前 600 字，表达不了「部分隐藏」。过滤在分页之前。
    """
    return await request.app.state.review_repo.list_reports(
        limit=limit,
        offset=offset,
        content_id=content_id,
        app_visible_only=app_channel(request),
    )


@router.get("/api/v1/reports/{report_id}", response_model=Report)
async def get_report(report_id: str, request: Request) -> Report:
    report = await request.app.state.review_repo.get_report(
        report_id, app_visible_only=app_channel(request)
    )
    if report is None:
        raise HTTPException(status_code=404, detail=f"报告不存在: {report_id}")
    return report
