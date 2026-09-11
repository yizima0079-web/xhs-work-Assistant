"""app 采集申请 API：提交意图 / 查进度 / 放行或驳回。

**这条链路的权限是整个模块的重点**，见 auth.py::_APP_TOKEN_DENY_PREFIXES：

    POST   /api/v1/collection-requests        设备令牌放行 —— 纯写库，零外呼
    GET    /api/v1/collection-requests        设备令牌放行 —— app 看自己的申请进度
    PATCH  /api/v1/collection-requests/{id}   设备令牌 401 —— 只有管理员 Cookie 能决策

尾斜杠就是这个分界：`.../collection-requests/` 是前缀，只命中有 id 的决策动作，
不命中上面的提交与列表。**改动 `_APP_TOKEN_DENY_PREFIXES` 时务必保住这个尾斜杠。**
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from app.auth import session_user
from app.config import settings
from app.domain.enums import RequestStatus, RunStatus, TargetType
from app.domain.models import CollectionRequest, CollectionRun
from app.schemas.api import CreateCollectionRequestRequest, DecideCollectionRequestRequest

router = APIRouter()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.post("/api/v1/collection-requests", response_model=CollectionRequest, status_code=201)
async def create_request(
    body: CreateCollectionRequestRequest, request: Request
) -> CollectionRequest:
    """app 提交采集意图。**这里不触发任何采集**，只写一行 pending。"""
    # 上限在服务端钳一次，不信任客户端 —— AGENTS.md「单任务默认最多 10 条」。
    max_items = max(1, min(body.max_items, settings.collection_request_max_items))
    req = CollectionRequest(
        id=uuid.uuid4().hex,
        platform=body.platform,
        target_type=TargetType.KEYWORD,
        target=body.target.strip(),
        max_items=max_items,
        status=RequestStatus.PENDING,
        requested_by="app",
        created_at=_now_iso(),
    )
    await request.app.state.collection_request_repo.create(req)
    return req


@router.get("/api/v1/collection-requests", response_model=list[CollectionRequest])
async def list_requests(
    request: Request, limit: int = 50, offset: int = 0, status: str | None = None
) -> list[CollectionRequest]:
    return await request.app.state.collection_request_repo.list(
        limit=limit, offset=offset, status=status
    )


@router.patch("/api/v1/collection-requests/{request_id}", response_model=CollectionRequest)
async def decide_request(
    request_id: str, body: DecideCollectionRequestRequest, request: Request
) -> CollectionRequest:
    """管理员放行 / 驳回。

    放行 = 建一个真实的 CollectionRun 并交给 collector —— 与 POST /collection-runs 是
    同一件事，只是多回填 run_id / request_id 把两头串起来。只在 pending 时允许，
    否则重复放行会造出第二个真实采集任务。
    """
    repo = request.app.state.collection_request_repo
    req = await repo.get(request_id)
    if req is None:
        raise HTTPException(status_code=404, detail="collection request not found")
    if req.status is not RequestStatus.PENDING:
        raise HTTPException(
            status_code=409, detail=f"申请已是 {req.status.value}，不能重复决策"
        )

    req.decided_at = _now_iso()
    req.decided_by = session_user(request) or "admin"
    req.note = body.note or None

    if body.action == "reject":
        req.status = RequestStatus.REJECTED
        await repo.update(req)
        return req

    # 放行：与 collection_runs.create_run 同一套创建逻辑，额外带上回指字段。
    run_id = uuid.uuid4().hex
    run = CollectionRun(
        id=run_id,
        request_id=req.id,
        platform=req.platform,
        target_type=req.target_type,
        target=req.target,
        max_items=req.max_items,
        status=RunStatus.QUEUED,
    )
    await request.app.state.run_repo.create(run)
    req.status = RequestStatus.APPROVED
    req.run_id = run_id
    await repo.update(req)
    asyncio.create_task(request.app.state.collector.execute(run_id))
    return req
