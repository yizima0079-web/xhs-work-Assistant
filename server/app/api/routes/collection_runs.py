"""采集任务 API：创建 / 查询 / 取消。"""
from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, HTTPException, Request

from app.domain.enums import RunStatus
from app.domain.models import CollectionRun
from app.schemas.api import CollectionRunDetail, CreateCollectionRunRequest

router = APIRouter()

_TERMINAL = {RunStatus.SUCCESS, RunStatus.PARTIAL, RunStatus.BLOCKED, RunStatus.FAILED, RunStatus.CANCELLED}


@router.post("/api/v1/collection-runs", response_model=CollectionRun, status_code=201)
async def create_run(body: CreateCollectionRunRequest, request: Request) -> CollectionRun:
    run_id = uuid.uuid4().hex
    run = CollectionRun(
        id=run_id,
        platform=body.platform,
        target_type=body.target_type,
        target=body.target,
        max_items=body.max_items,
        status=RunStatus.QUEUED,
    )
    await request.app.state.run_repo.create(run)
    asyncio.create_task(request.app.state.collector.execute(run_id))
    return run


@router.get("/api/v1/collection-runs", response_model=list[CollectionRun])
async def list_runs(request: Request, limit: int = 50, offset: int = 0) -> list[CollectionRun]:
    return await request.app.state.run_repo.list(limit=limit, offset=offset)


@router.get("/api/v1/collection-runs/{run_id}", response_model=CollectionRunDetail)
async def get_run(run_id: str, request: Request) -> CollectionRunDetail:
    run = await request.app.state.run_repo.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    events = await request.app.state.event_repo.list_by_run(run_id)
    return CollectionRunDetail(run=run, events=events)


@router.post("/api/v1/collection-runs/{run_id}/cancel", response_model=CollectionRun)
async def cancel_run(run_id: str, request: Request) -> CollectionRun:
    run = await request.app.state.run_repo.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    await request.app.state.collector.cancel(run_id)
    if run.status not in _TERMINAL:
        run.status = RunStatus.CANCELLED
        await request.app.state.run_repo.update(run)
    return run
