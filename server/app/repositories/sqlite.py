"""SQLite 仓储实现（aiosqlite，短连接 + WAL）。"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from app.db import connect
from app.domain.enums import (
    ClaimStatus,
    ClaimType,
    ContentType,
    ErrorCategory,
    EvidenceKind,
    ReviewStatus,
    RunStatus,
    TargetType,
)
from app.domain.models import (
    Analysis,
    Claim,
    CollectionEvent,
    CollectionRun,
    Content,
    Engagement,
    Evidence,
    MediaRef,
    RawAsset,
    Report,
    ReviewDecision,
)


def _row(row) -> dict:
    return dict(row)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _hidden_content_ids() -> set[str]:
    """被 app 隐藏的 content_id 集合（`app_hidden_at` 非空）。

    隐藏是低频动作，这个集合通常很小（几条）。凡是「没有外键可 JOIN、只能在
    Python 侧判可见性」的地方（报告引用、证据来源）都取它一次做交集，
    比按行逐条发查询（N+1，每条还要 IN 展开）更省。

    **注意方向**：这里返回的是「已隐藏」而不是「可见」。调用方写成
    `... & hidden`（命中即隐藏）比 `... - visible` 更不容易写反。
    """
    async with connect() as db:
        cur = await db.execute(
            "SELECT content_id FROM contents WHERE app_hidden_at IS NOT NULL"
        )
        return {_row(r)["content_id"] for r in await cur.fetchall()}


async def _resolve_active_batch(db, content_id: str) -> str | None:
    """生效审核批次：contents.active_batch_id 为空时回落为 batch_seq 最大的一批。

    迁移前的历史行 batch_id='' 且 batch_seq=1，天然成为「第 1 版」，无需数据回填。
    无任何批次时返回 None（调用方据此返回空列表，而不是静默返回全部批次）。
    """
    cur = await db.execute(
        "SELECT active_batch_id FROM contents WHERE content_id=?", (content_id,)
    )
    row = await cur.fetchone()
    active = _row(row)["active_batch_id"] if row else None
    if active:
        return active
    cur = await db.execute(
        "SELECT batch_id FROM claims WHERE content_id=? "
        "ORDER BY batch_seq DESC, rowid DESC LIMIT 1",
        (content_id,),
    )
    row = await cur.fetchone()
    return _row(row)["batch_id"] if row else None


def _run_latency_ms(run: CollectionRun) -> int | None:
    """端到端耗时（ms）：started_at → finished_at。

    算在仓储层而不是采集侧，是为了让「写库的那一份」和「展示的那一份」永远同源。
    两端时间戳缺失或不可解析 → None：宁可为空，也不写 0 假装瞬间完成。
    """
    if not run.started_at or not run.finished_at:
        return None
    try:
        delta = datetime.fromisoformat(run.finished_at) - datetime.fromisoformat(run.started_at)
    except ValueError:
        return None
    return max(int(delta.total_seconds() * 1000), 0)


class SqliteRunRepository:
    async def create(self, run: CollectionRun) -> None:
        async with connect() as db:
            await db.execute(
                """INSERT INTO collection_runs
                (id, request_id, platform, target_type, target, max_items, status,
                 started_at, finished_at, latency_ms, error_code, error_category,
                 items_found, items_saved, retry_count, rate_limit_signal, tool_version)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run.id, run.request_id, run.platform, run.target_type.value, run.target,
                    run.max_items, run.status.value, run.started_at, run.finished_at,
                    _run_latency_ms(run), run.error_code,
                    run.error_category.value if run.error_category else None,
                    run.items_found, run.items_saved, run.retry_count,
                    run.rate_limit_signal, run.tool_version,
                ),
            )
            await db.commit()

    async def get(self, run_id: str) -> CollectionRun | None:
        async with connect() as db:
            cur = await db.execute("SELECT * FROM collection_runs WHERE id = ?", (run_id,))
            row = await cur.fetchone()
            return _run_from_row(_row(row)) if row else None

    async def update(self, run: CollectionRun) -> None:
        async with connect() as db:
            await db.execute(
                """UPDATE collection_runs SET
                   request_id=?, platform=?, target_type=?, target=?, max_items=?, status=?,
                   started_at=?, finished_at=?, latency_ms=?, error_code=?, error_category=?,
                   items_found=?, items_saved=?, retry_count=?, rate_limit_signal=?, tool_version=?
                   WHERE id=?""",
                (
                    run.request_id, run.platform, run.target_type.value, run.target, run.max_items,
                    run.status.value, run.started_at, run.finished_at,
                    _run_latency_ms(run), run.error_code,
                    run.error_category.value if run.error_category else None,
                    run.items_found, run.items_saved, run.retry_count,
                    run.rate_limit_signal, run.tool_version, run.id,
                ),
            )
            await db.commit()

    async def list(self, limit: int = 50, offset: int = 0) -> list[CollectionRun]:
        async with connect() as db:
            cur = await db.execute(
                "SELECT * FROM collection_runs ORDER BY rowid DESC LIMIT ? OFFSET ?", (limit, offset)
            )
            rows = await cur.fetchall()
            return [_run_from_row(_row(r)) for r in rows]


class SqliteEventRepository:
    async def add(self, event: CollectionEvent) -> None:
        async with connect() as db:
            await db.execute(
                """INSERT INTO collection_events (run_id, seq, ts, level, category, code, message)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    event.run_id, event.seq, event.ts, event.level,
                    event.category.value if event.category else None,
                    event.code, event.message,
                ),
            )
            await db.commit()

    async def list_by_run(self, run_id: str) -> list[CollectionEvent]:
        async with connect() as db:
            cur = await db.execute(
                "SELECT * FROM collection_events WHERE run_id=? ORDER BY seq, id", (run_id,)
            )
            rows = await cur.fetchall()
            out = []
            for r in rows:
                d = _row(r)
                out.append(
                    CollectionEvent(
                        run_id=d["run_id"], seq=d["seq"], ts=d["ts"], level=d["level"],
                        category=ErrorCategory(d["category"]) if d["category"] else None,
                        code=d["code"], message=d["message"] or "",
                    )
                )
            return out


class SqliteContentRepository:
    async def upsert(self, content: Content) -> bool:
        """初次入库用（ON CONFLICT DO NOTHING）：返回是否新插入；已存在不覆盖。"""
        async with connect() as db:
            cur = await db.execute(
                """INSERT INTO contents
                (content_id, platform, platform_item_id, content_type, canonical_url,
                 author_id, author_name, published_at, collected_at, title, text,
                 cover_url, cover_local, tags, media, engagement, raw_refs, review_status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(platform, platform_item_id) DO NOTHING""",
                (
                    content.content_id, content.platform, content.platform_item_id,
                    content.content_type.value if content.content_type else None,
                    content.canonical_url, content.author_id, content.author_name,
                    content.published_at, content.collected_at, content.title, content.text,
                    content.cover_url, content.cover_local,
                    json.dumps(content.tags, ensure_ascii=False),
                    json.dumps([m.model_dump() for m in content.media], ensure_ascii=False),
                    json.dumps(content.engagement.model_dump(), ensure_ascii=False),
                    json.dumps(content.raw_refs, ensure_ascii=False),
                    content.review_status.value,
                ),
            )
            await db.commit()
            return cur.rowcount > 0

    async def update_content(self, content: Content) -> bool:
        """整行覆盖（note 详情补抓后回写更全字段，含 cover_local）。不存在返回 False。"""
        async with connect() as db:
            cur = await db.execute(
                """UPDATE contents SET
                   platform_item_id=?, content_type=?, canonical_url=?, author_id=?,
                   author_name=?, published_at=?, collected_at=?, title=?, text=?,
                   cover_url=?, cover_local=?, tags=?, media=?, engagement=?, raw_refs=?,
                   review_status=?
                   WHERE content_id=?""",
                (
                    content.platform_item_id,
                    content.content_type.value if content.content_type else None,
                    content.canonical_url, content.author_id, content.author_name,
                    content.published_at, content.collected_at, content.title, content.text,
                    content.cover_url, content.cover_local,
                    json.dumps(content.tags, ensure_ascii=False),
                    json.dumps([m.model_dump() for m in content.media], ensure_ascii=False),
                    json.dumps(content.engagement.model_dump(), ensure_ascii=False),
                    json.dumps(content.raw_refs, ensure_ascii=False),
                    content.review_status.value, content.content_id,
                ),
            )
            await db.commit()
            return cur.rowcount > 0

    async def get(
        self, content_id: str, app_visible_only: bool = False
    ) -> Content | None:
        """`app_visible_only` 下隐藏内容一律返回 None —— 表现为 **404 而不是 403**。

        403 等于告诉 app「这条内容存在、只是被隐藏了」，那就成了一个存在性预言机。
        """
        sql = "SELECT * FROM contents WHERE content_id = ?"
        if app_visible_only:
            sql += " AND app_hidden_at IS NULL"
        async with connect() as db:
            cur = await db.execute(sql, (content_id,))
            row = await cur.fetchone()
            return _content_from_row(_row(row)) if row else None

    async def list(
        self,
        limit: int = 50,
        offset: int = 0,
        platform: str | None = None,
        app_visible_only: bool = False,
        app_hidden: bool | None = None,
    ) -> list[Content]:
        """`app_visible_only` = app 通道（隐藏行不返回）；`app_hidden` = web 端的显式筛选。

        两者语义独立：前者由通道决定、默认关（不传 = 全量，web 行为逐字节不变），
        后者只给 web 看板做「只看被 app 藏起来的」筛选。同时传也不会矛盾 ——
        app 通道已经把隐藏行拿光了，再筛 `app_hidden=True` 只会得到空列表。
        """
        where: list[str] = []
        params: list = []
        if platform:
            where.append("platform=?")
            params.append(platform)
        if app_visible_only:
            where.append("app_hidden_at IS NULL")
        if app_hidden is True:
            where.append("app_hidden_at IS NOT NULL")
        elif app_hidden is False:
            where.append("app_hidden_at IS NULL")
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        async with connect() as db:
            cur = await db.execute(
                f"SELECT * FROM contents{clause} ORDER BY rowid DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            )
            rows = await cur.fetchall()
            return [_content_from_row(_row(r)) for r in rows]

    async def search(
        self,
        query: str,
        exclude_content_id: str | None = None,
        limit: int = 20,
        app_visible_only: bool = False,
    ) -> list[Content]:
        """关键词检索 title/text。query 为空格分隔的关键词（service 侧负责切词）。

        app 通道必须过滤：审核与分析的候选内容会**落进派生数据**——候选的
        title/text 前 80 字写进 `evidence.excerpt`（review.py:_clean_judgment），
        候选 id 进 `payload.comparison.baseline_content_ids`。不过滤的话，隐藏之后
        内容还会继续"长进"库里新产生的证据与报告里。
        """
        terms = [t for t in query.split() if t]
        if not terms:
            return []
        clauses = []
        params: list[object] = []
        for term in terms:
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            clauses.append("(title LIKE ? ESCAPE '\\' OR text LIKE ? ESCAPE '\\')")
            pattern = f"%{escaped}%"
            params.extend([pattern, pattern])
        sql = f"SELECT * FROM contents WHERE ({' OR '.join(clauses)})"
        if exclude_content_id:
            sql += " AND content_id != ?"
            params.append(exclude_content_id)
        if app_visible_only:
            sql += " AND app_hidden_at IS NULL"
        sql += " ORDER BY rowid DESC LIMIT ?"
        params.append(limit)
        async with connect() as db:
            cur = await db.execute(sql, params)
            rows = await cur.fetchall()
            return [_content_from_row(_row(r)) for r in rows]

    async def update_review_status(self, content_id: str, status: ReviewStatus) -> bool:
        async with connect() as db:
            cur = await db.execute(
                "UPDATE contents SET review_status=? WHERE content_id=?",
                (status.value, content_id),
            )
            await db.commit()
            return cur.rowcount > 0

    async def summary(self, limit: int = 20, app_visible_only: bool = False) -> list[dict]:
        """内容池概览：每条内容的派生计数，供看板一次取齐（替代逐条探测的 N+1）。

        只读聚合，不改任何状态。reports 无 FK，按既有 `LIKE '%"id"%'` 粗筛 +
        Python 精确成员判定统计，避免 'abc' 误配 'abcd'。

        app 可见性过滤**只加在取 id 的那一段**，下游全部自动正确：ids 是所有派生
        计数的循环上界，隐藏项既不出现在结果里，也不会把它的分析/断言/KB 文档
        算到别人头上。
        """
        sql = "SELECT content_id, app_hidden_at FROM contents"
        if app_visible_only:
            sql += " WHERE app_hidden_at IS NULL"
        sql += " ORDER BY rowid DESC LIMIT ?"
        async with connect() as db:
            cur = await db.execute(sql, (limit,))
            rows = [_row(r) for r in await cur.fetchall()]
            if not rows:
                return []
            ids = [r["content_id"] for r in rows]
            out = {
                r["content_id"]: {
                    "content_id": r["content_id"], "analysis_count": 0, "latest_analysis_id": None,
                    "latest_analysis_at": None, "report_count": 0, "claim_count": 0,
                    "review_batch_count": 0, "kb_doc_count": 0,
                    "app_hidden_at": r["app_hidden_at"],
                }
                for r in rows
            }
            marks = ",".join("?" * len(ids))

            cur = await db.execute(
                f"SELECT content_id, analysis_id, created_at FROM analyses "
                f"WHERE content_id IN ({marks}) ORDER BY rowid",
                ids,
            )
            for d in (_row(r) for r in await cur.fetchall()):
                item = out[d["content_id"]]
                item["analysis_count"] += 1
                item["latest_analysis_id"] = d["analysis_id"]   # rowid 递增 → 最后一条即最新
                item["latest_analysis_at"] = d["created_at"]

            cur = await db.execute(
                f"SELECT content_id, COUNT(*) AS n, COUNT(DISTINCT batch_id) AS batches "
                f"FROM claims WHERE content_id IN ({marks}) GROUP BY content_id",
                ids,
            )
            for d in (_row(r) for r in await cur.fetchall()):
                out[d["content_id"]]["claim_count"] = d["n"]
                out[d["content_id"]]["review_batch_count"] = d["batches"]

            # KB 文档：content 类按 content_id 直配；analysis 类按该内容的 analysis_id 配
            cur = await db.execute(
                f"SELECT analysis_id, content_id FROM analyses WHERE content_id IN ({marks})",
                ids,
            )
            analysis_owner = {
                _row(r)["analysis_id"]: _row(r)["content_id"] for r in await cur.fetchall()
            }
            # 无分析时 IN (NULL) 恒不匹配，避免 IN () 的语法错误
            ana_marks = ",".join("?" * len(analysis_owner)) or "NULL"
            cur = await db.execute(
                f"SELECT source_type, source_id FROM kb_documents "
                f"WHERE (source_type='content' AND source_id IN ({marks})) "
                f"OR (source_type='analysis' AND source_id IN ({ana_marks}))",
                ids + list(analysis_owner.keys()),
            )
            for d in (_row(r) for r in await cur.fetchall()):
                cid = (
                    d["source_id"] if d["source_type"] == "content"
                    else analysis_owner.get(d["source_id"])
                )
                if cid in out:
                    out[cid]["kb_doc_count"] += 1

            # 报告无 FK：多取一段余量后精确判定
            cur = await db.execute(
                "SELECT content_ids FROM reports WHERE "
                + " OR ".join(["content_ids LIKE ?"] * len(ids)),
                [f'%"{cid}"%' for cid in ids],
            )
            for d in (_row(r) for r in await cur.fetchall()):
                try:
                    listed = json.loads(d["content_ids"] or "[]")
                except ValueError:
                    continue
                for cid in ids:
                    if cid in listed:
                        out[cid]["report_count"] += 1

            return [out[cid] for cid in ids]

    async def set_app_hidden(self, content_id: str, hidden: bool) -> bool:
        """切换 app 端可见性标记。返回 False = 内容不存在（路由据此 404）。

        **重复隐藏不刷时间戳**：`COALESCE(app_hidden_at, ?)` 保住第一次的时刻 ——
        「自何时起被隐藏」要能追溯，被一次重放刷成新时间就失去意义了。
        取消隐藏置回 NULL（不是空串 —— 全部过滤条件都写 `IS NULL`）。

        `rowcount` 判存在性：UPDATE 没命中任何行即为 0。SQLite 里「行命中但值没变」
        也算 1，所以取消隐藏一条本就可见的内容同样返回 True —— 幂等，与路由
        「只有内容不存在才 404」的语义一致。
        """
        if hidden:
            sql = (
                "UPDATE contents SET app_hidden_at=COALESCE(app_hidden_at, ?) "
                "WHERE content_id=?"
            )
            params: tuple = (_now_iso(), content_id)
        else:
            sql = "UPDATE contents SET app_hidden_at=NULL WHERE content_id=?"
            params = (content_id,)
        async with connect() as db:
            cur = await db.execute(sql, params)
            await db.commit()
            return cur.rowcount > 0

    async def delete_content(self, content_id: str) -> dict | None:
        """删除一条内容及其全部派生数据：单事务、FK 逆序，不留孤儿。

        顺序：evidence → review_decisions → claims → analyses → kb_chunks → kb_qa_pairs
        → kb_documents（content 与 analysis 两类来源）→ raw_assets
        → reports 摘除该 id（摘空则整篇删）→ contents 本体。
        返回各表受影响行数供回执与测试断言；内容不存在返回 None（路由据此 404，
        避免「删了但什么都没删」被当成成功）。
        """
        async with connect() as db:
            cur = await db.execute(
                "SELECT cover_local FROM contents WHERE content_id=?", (content_id,)
            )
            row = await cur.fetchone()
            if row is None:
                return None

            counts: dict[str, int] = {}
            cur = await db.execute(
                "SELECT analysis_id FROM analyses WHERE content_id=?", (content_id,)
            )
            analysis_ids = [_row(r)["analysis_id"] for r in await cur.fetchall()]

            # KB 文档先定位（content 直配 + analysis 派生），再按 doc 清子表
            doc_ids: set[str] = set()
            cur = await db.execute(
                "SELECT doc_id FROM kb_documents WHERE source_type='content' AND source_id=?",
                (content_id,),
            )
            doc_ids |= {_row(r)["doc_id"] for r in await cur.fetchall()}
            if analysis_ids:
                marks = ",".join("?" * len(analysis_ids))
                cur = await db.execute(
                    f"SELECT doc_id FROM kb_documents WHERE source_type='analysis' "
                    f"AND source_id IN ({marks})",
                    analysis_ids,
                )
                doc_ids |= {_row(r)["doc_id"] for r in await cur.fetchall()}
            for doc_id in doc_ids:
                cur = await db.execute("DELETE FROM kb_chunks WHERE doc_id=?", (doc_id,))
                counts["kb_chunks"] = counts.get("kb_chunks", 0) + cur.rowcount
                cur = await db.execute("DELETE FROM kb_qa_pairs WHERE doc_id=?", (doc_id,))
                counts["kb_qa_pairs"] = counts.get("kb_qa_pairs", 0) + cur.rowcount
                await db.execute("DELETE FROM kb_documents WHERE doc_id=?", (doc_id,))
            counts["kb_documents"] = len(doc_ids)

            for table, sql in (
                ("evidence",
                 "DELETE FROM evidence WHERE claim_id IN "
                 "(SELECT claim_id FROM claims WHERE content_id=?)"),
                ("review_decisions",
                 "DELETE FROM review_decisions WHERE claim_id IN "
                 "(SELECT claim_id FROM claims WHERE content_id=?)"),
                ("claims", "DELETE FROM claims WHERE content_id=?"),
                ("analyses", "DELETE FROM analyses WHERE content_id=?"),
                ("raw_assets", "DELETE FROM raw_assets WHERE content_id=?"),
            ):
                cur = await db.execute(sql, (content_id,))
                counts[table] = cur.rowcount

            # 报告未被 FK 覆盖：含该 id 的摘掉它，摘空则整篇删除
            cur = await db.execute(
                "SELECT report_id, content_ids FROM reports WHERE content_ids LIKE ?",
                (f'%"{content_id}"%',),
            )
            reports_touched = 0
            for d in (_row(r) for r in await cur.fetchall()):
                try:
                    listed = json.loads(d["content_ids"] or "[]")
                except ValueError:
                    continue
                if content_id not in listed:
                    continue
                remaining = [c for c in listed if c != content_id]
                reports_touched += 1
                if remaining:
                    await db.execute(
                        "UPDATE reports SET content_ids=? WHERE report_id=?",
                        (json.dumps(remaining, ensure_ascii=False), d["report_id"]),
                    )
                else:
                    await db.execute("DELETE FROM reports WHERE report_id=?", (d["report_id"],))
            counts["reports_touched"] = reports_touched

            cur = await db.execute("DELETE FROM contents WHERE content_id=?", (content_id,))
            counts["contents"] = cur.rowcount
            await db.commit()
            counts["cover_local"] = row["cover_local"]
            return counts


class SqliteRawAssetRepository:
    async def add(self, asset: RawAsset) -> None:
        async with connect() as db:
            await db.execute(
                """INSERT INTO raw_assets (id, run_id, content_id, kind, mime, sha256, bytes, storage_path, collected_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    asset.id, asset.run_id, asset.content_id, asset.kind, asset.mime,
                    asset.sha256, asset.bytes, asset.storage_path, asset.collected_at,
                ),
            )
            await db.commit()

    async def list_by_run(self, run_id: str) -> list[RawAsset]:
        async with connect() as db:
            cur = await db.execute("SELECT * FROM raw_assets WHERE run_id=? ORDER BY rowid", (run_id,))
            rows = await cur.fetchall()
            out = []
            for r in rows:
                d = _row(r)
                out.append(
                    RawAsset(
                        id=d["id"], run_id=d["run_id"], content_id=d["content_id"],
                        kind=d["kind"], mime=d["mime"] or "application/json", sha256=d["sha256"],
                        bytes=d["bytes"] or 0, storage_path=d["storage_path"], collected_at=d["collected_at"],
                    )
                )
            return out


# ---- 行 -> 模型 ----
def _run_from_row(d: dict) -> CollectionRun:
    return CollectionRun(
        id=d["id"], request_id=d["request_id"], platform=d["platform"],
        target_type=TargetType(d["target_type"]), target=d["target"],
        max_items=d["max_items"] or 20,
        status=RunStatus(d["status"]), started_at=d["started_at"], finished_at=d["finished_at"],
        latency_ms=d["latency_ms"],
        error_code=d["error_code"],
        error_category=ErrorCategory(d["error_category"]) if d["error_category"] else None,
        items_found=d["items_found"] or 0, items_saved=d["items_saved"] or 0,
        retry_count=d["retry_count"] or 0, rate_limit_signal=d["rate_limit_signal"] or 0,
        tool_version=d["tool_version"],
    )


def _content_from_row(d: dict) -> Content:
    media = [MediaRef(**m) for m in (json.loads(d["media"]) if d["media"] else [])]
    eng = json.loads(d["engagement"]) if d["engagement"] else {}
    return Content(
        content_id=d["content_id"], platform=d["platform"], platform_item_id=d["platform_item_id"],
        content_type=ContentType(d["content_type"]) if d["content_type"] else None,
        canonical_url=d["canonical_url"], author_id=d["author_id"], author_name=d["author_name"],
        published_at=d["published_at"], collected_at=d["collected_at"], title=d["title"], text=d["text"],
        cover_url=d.get("cover_url"), cover_local=d.get("cover_local"),
        tags=json.loads(d["tags"]) if d.get("tags") else [],
        media=media, engagement=Engagement(**eng),
        raw_refs=json.loads(d["raw_refs"]) if d["raw_refs"] else [],
        review_status=ReviewStatus(d["review_status"]) if d["review_status"] else ReviewStatus.PENDING,
        active_batch_id=d.get("active_batch_id") or None,
        # 迁移列，老库/直接构造的行可能没有这个键 —— 与相邻的 .get 同款兜底
        app_hidden_at=d.get("app_hidden_at"),
    )


def _claim_from_row(d: dict) -> Claim:
    return Claim(
        claim_id=d["claim_id"], content_id=d["content_id"], text=d["text"],
        status=ClaimStatus(d["status"]) if d["status"] else ClaimStatus.UNVERIFIED,
        confidence=d["confidence"],
        claim_type=ClaimType(d["claim_type"]) if d["claim_type"] else None,
        meta=json.loads(d["meta"]) if d["meta"] else {},
        batch_id=d["batch_id"] or "",
        batch_seq=d["batch_seq"] if d["batch_seq"] else 1,
        created_at=d["created_at"], updated_at=d["updated_at"],
    )


def _evidence_from_row(d: dict) -> Evidence:
    return Evidence(
        evidence_id=d["evidence_id"], claim_id=d["claim_id"],
        source_kind=EvidenceKind(d["source_kind"]),
        source_ref=d["source_ref"], excerpt=d["excerpt"] or "",
        supports=d["supports"] if d["supports"] is not None else None,
        strength=d["strength"] or 1.0, collected_at=d["collected_at"],
    )


def _decision_from_row(d: dict) -> ReviewDecision:
    return ReviewDecision(
        decision_id=d["decision_id"], claim_id=d["claim_id"],
        status=ClaimStatus(d["status"]),
        rationale=d["rationale"] or "", reviewer=d["reviewer"] or "rule-engine",
        evidence_ids=json.loads(d["evidence_ids"]) if d["evidence_ids"] else [],
        created_at=d["created_at"],
    )


def _report_from_row(d: dict) -> Report:
    return Report(
        report_id=d["report_id"], title=d["title"],
        content_ids=json.loads(d["content_ids"]) if d["content_ids"] else [],
        payload=json.loads(d["payload"]) if d["payload"] else {},
        markdown=d["markdown"] or "", created_at=d["created_at"],
        schema_version=d["schema_version"] or "1.0",
    )


def _analysis_from_row(d: dict) -> Analysis:
    return Analysis(
        analysis_id=d["analysis_id"], content_id=d["content_id"],
        verdict=d["verdict"] or "uncertain",
        payload=json.loads(d["payload"]) if d["payload"] else {},
        markdown=d["markdown"] or "",
        compared_with=json.loads(d["compared_with"]) if d["compared_with"] else [],
        focus=d.get("focus") or "",
        created_at=d["created_at"],
        schema_version=d["schema_version"] or "1.0",
    )


class SqliteReviewRepository:
    async def add_claim(self, claim: Claim) -> None:
        async with connect() as db:
            await db.execute(
                """INSERT INTO claims (claim_id, content_id, text, status, confidence,
                                       claim_type, meta, batch_id, batch_seq,
                                       created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    claim.claim_id, claim.content_id, claim.text, claim.status.value,
                    claim.confidence,
                    claim.claim_type.value if claim.claim_type else None,
                    json.dumps(claim.meta, ensure_ascii=False),
                    claim.batch_id, claim.batch_seq,
                    claim.created_at, claim.updated_at,
                ),
            )
            await db.commit()

    async def get_claim(self, claim_id: str, app_visible_only: bool = False) -> Claim | None:
        """`app_visible_only`：源内容被 app 隐藏时返回 None（路由 404）。

        按 claim_id 直取的入口必须在这层挡 —— 断言本身是内容的拆解，且它的证据
        里带 `excerpt`（候选内容正文片段），绕过内容详情直接拿 claim_id 就能看到。
        INNER JOIN 同时挡住悬空来源（内容已不存在）。
        """
        if app_visible_only:
            sql = (
                "SELECT cl.* FROM claims cl JOIN contents ct ON cl.content_id = ct.content_id"
                " WHERE cl.claim_id = ? AND ct.app_hidden_at IS NULL"
            )
        else:
            sql = "SELECT cl.* FROM claims cl WHERE cl.claim_id = ?"
        async with connect() as db:
            cur = await db.execute(sql, (claim_id,))
            row = await cur.fetchone()
            return _claim_from_row(_row(row)) if row else None

    async def list_claims(self, content_id: str, batch_id: str | None = None) -> list[Claim]:
        """某内容某批次的断言。batch_id 为空 → 取生效批次（见 _resolve_active_batch）。"""
        async with connect() as db:
            if batch_id is None:
                batch_id = await _resolve_active_batch(db, content_id)
                if batch_id is None:
                    return []  # 无任何批次：不返回别的批次冒充「生效」
            cur = await db.execute(
                "SELECT * FROM claims WHERE content_id=? AND batch_id=? ORDER BY rowid",
                (content_id, batch_id),
            )
            rows = await cur.fetchall()
            return [_claim_from_row(_row(r)) for r in rows]

    async def list_review_batches(self, content_id: str) -> list[dict]:
        """审核批次列表（新→旧）。claim_count/created_at 取自该批次首批断言。"""
        async with connect() as db:
            active = await _resolve_active_batch(db, content_id)
            cur = await db.execute(
                "SELECT batch_id, MAX(batch_seq) AS batch_seq, COUNT(*) AS claim_count, "
                "MIN(created_at) AS created_at FROM claims WHERE content_id=? "
                "GROUP BY batch_id ORDER BY batch_seq DESC",
                (content_id,),
            )
            rows = await cur.fetchall()
        batches = [
            {
                "batch_id": d["batch_id"], "batch_seq": d["batch_seq"],
                "claim_count": d["claim_count"], "created_at": d["created_at"],
            }
            for d in (_row(r) for r in rows)
        ]
        # active 由 _resolve_active_batch 给出（含「未显式切换 → 最新一批」的回落），
        # 与 list_claims 用的是同一套判定，两处不会打架。
        for b in batches:
            b["active"] = b["batch_id"] == active
        return batches

    async def set_active_batch(self, content_id: str, batch_id: str) -> bool:
        """切换生效批次。batch_id 必须属于该内容，否则返回 False（不静默写入脏值）。"""
        async with connect() as db:
            cur = await db.execute(
                "SELECT 1 FROM claims WHERE content_id=? AND batch_id=? LIMIT 1",
                (content_id, batch_id),
            )
            if await cur.fetchone() is None:
                return False
            await db.execute(
                "UPDATE contents SET active_batch_id=? WHERE content_id=?",
                (batch_id, content_id),
            )
            await db.commit()
            return True

    async def update_claim(self, claim: Claim) -> None:
        async with connect() as db:
            await db.execute(
                """UPDATE claims SET text=?, status=?, confidence=?, claim_type=?, meta=?, updated_at=?
                   WHERE claim_id=?""",
                (
                    claim.text, claim.status.value, claim.confidence,
                    claim.claim_type.value if claim.claim_type else None,
                    json.dumps(claim.meta, ensure_ascii=False),
                    claim.updated_at, claim.claim_id,
                ),
            )
            await db.commit()

    async def add_evidence(self, evidence: Evidence) -> None:
        async with connect() as db:
            await db.execute(
                """INSERT INTO evidence (evidence_id, claim_id, source_kind, source_ref,
                                         excerpt, supports, strength, collected_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    evidence.evidence_id, evidence.claim_id,
                    evidence.source_kind.value, evidence.source_ref,
                    evidence.excerpt, evidence.supports, evidence.strength,
                    evidence.collected_at,
                ),
            )
            await db.commit()

    async def list_evidence(self, claim_id: str, app_visible_only: bool = False) -> list[Evidence]:
        """`app_visible_only`：证据指向的**源内容**被 app 隐藏时，该条证据不返回。

        断言可见不代表它的证据都可见 —— 证据是「拿哪条内容佐证这条断言」的记录，
        `excerpt` 里带来源内容正文的片段。命中一条隐藏内容当旁证，就等于把那条
        隐藏内容的正文片段搬到了可见断言的证据列表里。

        三值逻辑：只能用 Python 侧集合判定，不能写 `source_ref NOT IN (隐藏集合)` ——
        `source_ref` 为 NULL 时（手工/无来源的证据）该表达式求值为 NULL 而不是真，
        会把「没有来源引用」的证据一起误滤。这里显式列出三种放行情形。
        `source_kind` 非 content 时 `source_ref` 是别的表的 id，不是 content_id，
        与隐藏集合不可比，一并放行。
        """
        async with connect() as db:
            cur = await db.execute(
                "SELECT * FROM evidence WHERE claim_id=? ORDER BY rowid", (claim_id,)
            )
            rows = await cur.fetchall()
        items = [_evidence_from_row(_row(r)) for r in rows]
        if not app_visible_only:
            return items
        hidden = await _hidden_content_ids()
        if not hidden:
            return items
        return [
            e
            for e in items
            if e.source_kind != EvidenceKind.CONTENT
            or not e.source_ref
            or e.source_ref not in hidden
        ]

    async def add_decision(self, decision: ReviewDecision) -> None:
        async with connect() as db:
            await db.execute(
                """INSERT INTO review_decisions (decision_id, claim_id, status, rationale,
                                                 reviewer, evidence_ids, created_at)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    decision.decision_id, decision.claim_id, decision.status.value,
                    decision.rationale, decision.reviewer,
                    json.dumps(decision.evidence_ids, ensure_ascii=False),
                    decision.created_at,
                ),
            )
            await db.commit()

    async def list_decisions(self, claim_id: str) -> list[ReviewDecision]:
        async with connect() as db:
            cur = await db.execute(
                "SELECT * FROM review_decisions WHERE claim_id=? ORDER BY rowid", (claim_id,)
            )
            rows = await cur.fetchall()
            return [_decision_from_row(_row(r)) for r in rows]

    async def add_report(self, report: Report) -> None:
        async with connect() as db:
            await db.execute(
                """INSERT INTO reports (report_id, title, content_ids, payload, markdown,
                                        created_at, schema_version)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    report.report_id, report.title,
                    json.dumps(report.content_ids, ensure_ascii=False),
                    json.dumps(report.payload, ensure_ascii=False),
                    report.markdown, report.created_at, report.schema_version,
                ),
            )
            await db.commit()

    async def get_report(self, report_id: str, app_visible_only: bool = False) -> Report | None:
        """`app_visible_only`：报告引用的内容中**任一**被 app 隐藏 → 返回 None（路由 404）。

        为什么是 any 而不是 all：报告这种载体表达不了「部分隐藏」。
        `services/reporting.py` 把每条内容的 `title` 与 `text[:600]` 逐字拼进 prompt，
        渲染出的 markdown 结尾又把整个 payload 以 ```json``` 原样打印。所以只要报告里
        提到过一条隐藏内容，它的标题和正文就已经在报告全文里了 —— 「留着报告但抹掉
        那一条」做不到，只能整篇隐藏。
        """
        report = await self._load_report(report_id)
        if report is None:
            return None
        if app_visible_only and set(report.content_ids or []) & await _hidden_content_ids():
            return None
        return report

    async def _load_report(self, report_id: str) -> Report | None:
        async with connect() as db:
            cur = await db.execute("SELECT * FROM reports WHERE report_id = ?", (report_id,))
            row = await cur.fetchone()
            return _report_from_row(_row(row)) if row else None

    async def list_reports(
        self,
        limit: int = 50,
        offset: int = 0,
        content_id: str | None = None,
        app_visible_only: bool = False,
    ) -> list[Report]:
        """报告列表。content_id 非空时只返回「包含该内容」的报告（JSON 数组子串匹配）。

        content_ids 存的是 JSON 数组文本，这里用 LIKE 做粗筛后由 Python 精确判定，
        避免 'abc' 误匹配 'abcd'。

        `app_visible_only` 时：**先过滤、后分页**。报告引用关系没有 FK，过滤只能在
        Python 侧做，但顺序不能反 —— 先按 LIMIT 切页再过滤，app 会拿到不满一页的
        列表，按 limit 算的总页数全错。代价是隐藏项很多时会把它们也取回内存，
        但报告条数本来就有上限，可接受。
        """
        async with connect() as db:
            sql = "SELECT * FROM reports"
            params: list = []
            if content_id:
                sql += " WHERE content_ids LIKE ?"
                params.append(f'%"{content_id}"%')
            sql += " ORDER BY rowid DESC"
            if not app_visible_only:
                sql += " LIMIT ? OFFSET ?"
                params += [limit, offset]
            cur = await db.execute(sql, params)
            reports = [_report_from_row(_row(r)) for r in await cur.fetchall()]
        if content_id:
            reports = [r for r in reports if content_id in (r.content_ids or [])]
        if app_visible_only:
            hidden = await _hidden_content_ids()
            reports = [r for r in reports if not (set(r.content_ids or []) & hidden)]
            reports = reports[offset:offset + limit]  # 过滤之后才切页
        return reports

    async def save_review_batch(
        self,
        content_id: str,
        claims: list[Claim],
        evidences: list[Evidence],
        decisions: list[ReviewDecision],
        final_status: ReviewStatus,
        batch_id: str | None = None,
        replace: bool = False,
    ) -> str:
        """原子落一单审核结果：单连接单 commit，FK 顺序 claims→evidence→decisions→contents.update。

        **默认（replace=False）新增一个审核批次**：batch_seq = 该内容已有最大序号 + 1，
        旧批次的断言/证据/判定**原样保留**——这是「重审保留历史、可回看第 N 版」的基础。
        新批次自动成为生效批次（contents.active_batch_id）。

        replace=True 是**同批次覆盖**的重试语义，**必须显式给出 batch_id**：清掉该批次旧行再写，
        避免重试产生重复行。注意它只动本批次，不碰其他版本。

        返回最终使用的 batch_id。
        """
        if replace and not batch_id:
            # 不给出批次就等于「覆盖谁」没答案：旧写法会静默退化成追加一个批次，
            # 调用方以为清干净了、实际多出一版。宁可报错。
            raise ValueError("replace=True 必须显式指定 batch_id（覆盖的是哪一批）")
        async with connect() as db:
            if not batch_id:
                batch_id = uuid.uuid4().hex
            # 序号必须在删旧行之前读：先删再读会读到空表，一个原本是第 3 版的批次
            # 在覆盖重试后会被重新编号成第 1 版，顺序全乱。
            cur = await db.execute(
                "SELECT MAX(batch_seq) FROM claims WHERE content_id=? AND batch_id=?",
                (content_id, batch_id),
            )
            row = await cur.fetchone()
            batch_seq = (row[0] if row and row[0] else 0) or 0
            if not batch_seq:  # 该批次还没有行（新建，或覆盖重试时已被清空）
                cur = await db.execute(
                    "SELECT MAX(batch_seq) FROM claims WHERE content_id=?", (content_id,)
                )
                row = await cur.fetchone()
                batch_seq = ((row[0] or 0) if row else 0) + 1

            if replace:
                await db.execute(
                    "DELETE FROM evidence WHERE claim_id IN "
                    "(SELECT claim_id FROM claims WHERE content_id=? AND batch_id=?)",
                    (content_id, batch_id),
                )
                await db.execute(
                    "DELETE FROM review_decisions WHERE claim_id IN "
                    "(SELECT claim_id FROM claims WHERE content_id=? AND batch_id=?)",
                    (content_id, batch_id),
                )
                await db.execute(
                    "DELETE FROM claims WHERE content_id=? AND batch_id=?",
                    (content_id, batch_id),
                )

            for c in claims:
                c.batch_id = batch_id
                c.batch_seq = batch_seq
                # 普通 INSERT（非 OR REPLACE）：claim_id 撞车说明调用方复用了 id，
                # OR REPLACE 会把那条断言从旧批次悄悄搬到新批次，等于毁掉历史版本。
                await db.execute(
                    """INSERT INTO claims
                       (claim_id, content_id, text, status, confidence, claim_type, meta,
                        batch_id, batch_seq, created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        c.claim_id, c.content_id, c.text, c.status.value, c.confidence,
                        c.claim_type.value if c.claim_type else None,
                        json.dumps(c.meta, ensure_ascii=False),
                        c.batch_id, c.batch_seq,
                        c.created_at, c.updated_at,
                    ),
                )
            for e in evidences:
                await db.execute(
                    """INSERT INTO evidence (evidence_id, claim_id, source_kind, source_ref,
                                             excerpt, supports, strength, collected_at)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        e.evidence_id, e.claim_id, e.source_kind.value, e.source_ref,
                        e.excerpt, e.supports, e.strength, e.collected_at,
                    ),
                )
            for dec in decisions:
                await db.execute(
                    """INSERT INTO review_decisions
                       (decision_id, claim_id, status, rationale, reviewer, evidence_ids, created_at)
                       VALUES (?,?,?,?,?,?,?)""",
                    (
                        dec.decision_id, dec.claim_id, dec.status.value, dec.rationale,
                        dec.reviewer, json.dumps(dec.evidence_ids, ensure_ascii=False),
                        dec.created_at,
                    ),
                )
            # 新批次落库即成为生效批次：内容状态与「查看审核」默认都指向它。
            # 但**空批次不产生生效版本** —— 没有断言就没有可生效的东西。若照样写入
            # active_batch_id，会留下一个指向不存在批次的悬空指针：批次列表是从
            # claims 聚合出来的（list_review_batches），空批次压根列不出来，界面
            # 表现为「状态是 extracted、却没有任何批次处于生效」。置 NULL 后仍走
            # 回落规则（取 batch_seq 最大的一批），残留的历史批次不受影响。
            if claims:
                await db.execute(
                    "UPDATE contents SET review_status=?, active_batch_id=? WHERE content_id=?",
                    (final_status.value, batch_id, content_id),
                )
            else:
                await db.execute(
                    "UPDATE contents SET review_status=?, active_batch_id=NULL WHERE content_id=?",
                    (final_status.value, content_id),
                )
            await db.commit()
            return batch_id


class SqliteAnalysisRepository:
    async def add(self, analysis: Analysis) -> None:
        async with connect() as db:
            await db.execute(
                """INSERT INTO analyses (analysis_id, content_id, verdict, payload,
                                         markdown, compared_with, focus, created_at, schema_version)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    analysis.analysis_id, analysis.content_id, analysis.verdict,
                    json.dumps(analysis.payload, ensure_ascii=False),
                    analysis.markdown,
                    json.dumps(analysis.compared_with, ensure_ascii=False),
                    analysis.focus,
                    analysis.created_at, analysis.schema_version,
                ),
            )
            await db.commit()

    async def get(self, analysis_id: str, app_visible_only: bool = False) -> Analysis | None:
        """`app_visible_only`：源内容被 app 隐藏时返回 None（路由 404）。

        **这一层必须挡**：上层路由按 content_id 判可见性，挡不住「绕过内容详情、
        直接拿 analysis_id 来取分析全文」这条路 —— 而分析 payload 里就是整篇内容
        的拆解。

        用 INNER JOIN 而不是 LEFT JOIN + IS NULL：源内容已不存在（悬空来源）时
        也无行返回。悬空来源本就不该可见，INNER JOIN 天然表达这个语义，
        也不用担心三值逻辑。
        """
        if app_visible_only:
            sql = (
                "SELECT a.* FROM analyses a JOIN contents c ON a.content_id = c.content_id"
                " WHERE a.analysis_id = ? AND c.app_hidden_at IS NULL"
            )
        else:
            sql = "SELECT a.* FROM analyses a WHERE a.analysis_id = ?"
        async with connect() as db:
            cur = await db.execute(sql, (analysis_id,))
            row = await cur.fetchone()
            return _analysis_from_row(_row(row)) if row else None

    async def list_by_content(
        self,
        content_id: str,
        limit: int = 50,
        offset: int = 0,
        app_visible_only: bool = False,
    ) -> list[Analysis]:
        if app_visible_only:
            sql = (
                "SELECT a.* FROM analyses a JOIN contents c ON a.content_id = c.content_id"
                " WHERE a.content_id = ? AND c.app_hidden_at IS NULL"
                " ORDER BY a.created_at DESC, a.rowid DESC LIMIT ? OFFSET ?"
            )
        else:
            sql = (
                "SELECT a.* FROM analyses a WHERE a.content_id=?"
                " ORDER BY a.created_at DESC, a.rowid DESC LIMIT ? OFFSET ?"
            )
        async with connect() as db:
            cur = await db.execute(sql, (content_id, limit, offset))
            rows = await cur.fetchall()
            return [_analysis_from_row(_row(r)) for r in rows]
