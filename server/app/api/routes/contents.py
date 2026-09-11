"""内容 API：标准化内容列表 / 概览 / 详情 / 删除 / app 端可见性。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.auth import app_channel
from app.domain.models import Content
from app.schemas.api import ContentSummary

router = APIRouter()


# 注意：本路由必须声明在 /contents/{content_id} 之前。
# FastAPI 按注册顺序匹配，后声明的话 "summary" 会被当成一个 content_id 吃掉。
@router.get("/api/v1/contents/summary", response_model=list[ContentSummary])
async def summarize_contents(request: Request, limit: int = 20) -> list[ContentSummary]:
    """内容池概览：一次取齐每条内容的派生计数（分析/报告/断言/批次/KB 文档）。

    app 通道下被隐藏的内容连同它的计数一起消失 —— 否则会出现「列表里没有、
    概览里还在」的对不上，前端拿计数去渲染删除影响面也会算错。
    """
    return await request.app.state.content_repo.summary(
        limit=limit, app_visible_only=app_channel(request)
    )


@router.get("/api/v1/contents", response_model=list[Content])
async def list_contents(
    request: Request, limit: int = 50, offset: int = 0, platform: str | None = None
) -> list[Content]:
    """`app_visible_only` 在仓储层就下推进 WHERE，**先过滤再分页**。

    顺序反过来的话，app 会拿到「50 条里的 47 条」，前端按 limit 算的总页数全错。
    """
    return await request.app.state.content_repo.list(
        limit=limit,
        offset=offset,
        platform=platform,
        app_visible_only=app_channel(request),
    )


@router.get("/api/v1/contents/{content_id}", response_model=Content)
async def get_content(content_id: str, request: Request) -> Content:
    """app 通道下已隐藏的内容返回 **404 而不是 403**。

    403 等于告诉 app「这条内容存在、只是被隐藏了」，那就成了一个存在性预言机。
    """
    c = await request.app.state.content_repo.get(
        content_id, app_visible_only=app_channel(request)
    )
    if c is None:
        raise HTTPException(status_code=404, detail="content not found")
    return c


@router.post("/api/v1/contents/{content_id}/app-hidden", status_code=204)
async def hide_content_for_app(content_id: str, request: Request) -> None:
    """app 端「删除」= 软隐藏：只打 `app_hidden_at` 标记，数据与派生结果完整保留。

    web（管理员 Cookie）通道照常可见，可恢复、可真删。幂等：重复调用保持原时间戳，
    不刷新（「自何时起被隐藏」要能追溯）。内容不存在 → 404，不静默成功。
    """
    ok = await request.app.state.content_service.set_app_hidden(content_id, True)
    if not ok:
        raise HTTPException(status_code=404, detail=f"内容不存在: {content_id}")


@router.delete("/api/v1/contents/{content_id}/app-hidden", status_code=204)
async def restore_content_for_app(content_id: str, request: Request) -> None:
    """web 端「重新同步到 app 端」= 取消隐藏，app 下次拉取即恢复显示。

    **设备令牌调不到这里**（auth.`app_token_ok` 对它返回 False，无 Cookie 即 401）：
    若 app 能自行恢复，连点两下就把整套可见性机制绕过去了。放行权归管理端 Cookie。
    幂等：未隐藏的内容调用也返回 204。
    """
    ok = await request.app.state.content_service.set_app_hidden(content_id, False)
    if not ok:
        raise HTTPException(status_code=404, detail=f"内容不存在: {content_id}")


@router.delete("/api/v1/contents/{content_id}", status_code=204)
async def delete_content(content_id: str, request: Request) -> None:
    """删除内容及全部派生数据（断言/证据/判定/分析/KB 文档/报告引用/本地封面）。

    不可逆：前端必须先做二次确认并列出影响面。内容不存在 → 404，不静默成功。
    这是 web 端的「彻底删除」，与上面的 app 端软隐藏是两件事，语义不合并。
    """
    service = request.app.state.content_service
    counts = await service.delete_content(content_id)
    if counts is None:
        raise HTTPException(status_code=404, detail=f"内容不存在: {content_id}")
