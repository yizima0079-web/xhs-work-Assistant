"""app 采集申请仓储（独立文件，与 kb.py 同理：不往 sqlite.py 里继续堆积）。

表结构见 schema.sql 的 collection_requests。状态机极简：pending → approved | rejected，
两个终态都不可回退（放行过的单子再放行会造出第二个真实采集任务）。
"""
from __future__ import annotations

from app.db import connect
from app.domain.enums import RequestStatus, TargetType
from app.domain.models import CollectionRequest


def _row(row) -> dict:
    return dict(row)


def _request_from_row(d: dict) -> CollectionRequest:
    return CollectionRequest(
        id=d["id"], platform=d["platform"],
        target_type=TargetType(d["target_type"]), target=d["target"],
        max_items=d["max_items"] or 10,
        status=RequestStatus(d["status"]), requested_by=d["requested_by"],
        run_id=d["run_id"], decided_at=d["decided_at"], decided_by=d["decided_by"],
        note=d["note"], created_at=d["created_at"],
    )


class SqliteCollectionRequestRepository:
    async def create(self, req: CollectionRequest) -> None:
        async with connect() as db:
            await db.execute(
                """INSERT INTO collection_requests
                (id, platform, target_type, target, max_items, status, requested_by,
                 run_id, decided_at, decided_by, note, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    req.id, req.platform, req.target_type.value, req.target, req.max_items,
                    req.status.value, req.requested_by, req.run_id, req.decided_at,
                    req.decided_by, req.note, req.created_at,
                ),
            )
            await db.commit()

    async def get(self, request_id: str) -> CollectionRequest | None:
        async with connect() as db:
            cur = await db.execute("SELECT * FROM collection_requests WHERE id = ?", (request_id,))
            row = await cur.fetchone()
            return _request_from_row(_row(row)) if row else None

    async def update(self, req: CollectionRequest) -> None:
        async with connect() as db:
            await db.execute(
                """UPDATE collection_requests SET
                   platform=?, target_type=?, target=?, max_items=?, status=?, requested_by=?,
                   run_id=?, decided_at=?, decided_by=?, note=?
                   WHERE id=?""",
                (
                    req.platform, req.target_type.value, req.target, req.max_items,
                    req.status.value, req.requested_by, req.run_id, req.decided_at,
                    req.decided_by, req.note, req.id,
                ),
            )
            await db.commit()

    async def list(
        self, limit: int = 50, offset: int = 0, status: str | None = None
    ) -> list[CollectionRequest]:
        """待放行的排最前 —— 看板第一眼要看到「有东西等我点」。"""
        async with connect() as db:
            if status:
                cur = await db.execute(
                    "SELECT * FROM collection_requests WHERE status = ? "
                    "ORDER BY rowid DESC LIMIT ? OFFSET ?",
                    (status, limit, offset),
                )
            else:
                cur = await db.execute(
                    "SELECT * FROM collection_requests "
                    "ORDER BY (status = 'pending') DESC, rowid DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            rows = await cur.fetchall()
            return [_request_from_row(_row(r)) for r in rows]
