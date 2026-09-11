"""SQLite 知识库仓储：kb_documents / kb_chunks / kb_qa_pairs。向量按 JSON 存取，相似度在 service 层算。"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.db import connect
from app.domain.models import KbChunk, KbDocument, KbQaPair


def _doc_from_row(d: dict) -> KbDocument:
    return KbDocument(
        doc_id=d["doc_id"], source_type=d["source_type"], source_id=d["source_id"],
        doc_type=d.get("doc_type") or "general",
        title=d["title"] or "", author=d["author"],
        tags=json.loads(d["tags"]) if d["tags"] else [],
        url=d["url"], content_hash=d["content_hash"] or "",
        raw_text=d.get("raw_text") or "", markdown=d.get("markdown") or "",
        status=d["status"] or "pending", chunk_count=d["chunk_count"] or 0,
        created_at=d["created_at"],
    )


def _chunk_from_row(d: dict) -> KbChunk:
    return KbChunk(
        chunk_id=d["chunk_id"], doc_id=d["doc_id"], chunk_index=d["chunk_index"] or 0,
        text=d["text"] or "",
        embedding=json.loads(d["embedding"]) if d["embedding"] else [],
        modality=d["modality"] or "text", image_url=d["image_url"],
        meta=json.loads(d["meta"]) if d["meta"] else {},
        created_at=d["created_at"],
    )


def _qa_from_row(d: dict) -> KbQaPair:
    return KbQaPair(
        qa_id=d["qa_id"], doc_id=d["doc_id"], qa_index=d["qa_index"] or 0,
        question=d["question"] or "", answer=d["answer"] or "",
        dimensions=json.loads(d["dimensions"]) if d["dimensions"] else {},
        evidence=json.loads(d["evidence"]) if d["evidence"] else [],
        tags=json.loads(d["tags"]) if d["tags"] else [],
        source_type=d["source_type"] or "manual", source_id=d["source_id"],
        source_url=d["source_url"], source_author=d["source_author"],
        status=d["status"] or "draft",
        created_at=d["created_at"], updated_at=d["updated_at"] or d["created_at"],
    )


def _placeholders(n: int) -> str:
    return ",".join("?" * n)


# ---- app 可见性：文档级判定（复用同一份 JOIN，避免三处各写一遍写歪）----
#
# `kb_documents.source_id` 的含义由 `source_type` 决定：content → content_id、
# analysis → analysis_id、manual → NULL（无源内容，人工录入的终态）。所以两种来源
# 必须分头接表，再接回 contents。
#
# 加 `cX.content_id IS NOT NULL` 是给悬空来源兜底：source_id 指向一条已经不存在的
# 内容时，LEFT JOIN 出来的整行是 NULL，若只判 `app_hidden_at IS NULL` 会因三值逻辑
# 求值为 TRUE 而**放行** —— 那等于「源越不存在越可见」，与直觉相反。
_APP_VISIBLE_DOC_JOINS = """
    LEFT JOIN contents c1 ON d.source_type = 'content'  AND d.source_id = c1.content_id
    LEFT JOIN analyses a  ON d.source_type = 'analysis' AND d.source_id = a.analysis_id
    LEFT JOIN contents c2 ON a.content_id = c2.content_id"""

_APP_VISIBLE_DOC_WHERE = """
       d.source_type = 'manual'
    OR (d.source_type = 'content'  AND c1.content_id IS NOT NULL AND c1.app_hidden_at IS NULL)
    OR (d.source_type = 'analysis' AND c2.content_id IS NOT NULL AND c2.app_hidden_at IS NULL)"""


class SqliteKnowledgeBaseRepository:
    async def add_document(self, doc: KbDocument) -> None:
        async with connect() as db:
            await db.execute(
                """INSERT INTO kb_documents
                   (doc_id, source_type, source_id, doc_type, title, author, tags, url,
                    content_hash, raw_text, markdown, status, chunk_count, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    doc.doc_id, doc.source_type, doc.source_id, doc.doc_type,
                    doc.title, doc.author,
                    json.dumps(doc.tags, ensure_ascii=False), doc.url,
                    doc.content_hash, doc.raw_text, doc.markdown,
                    doc.status, doc.chunk_count, doc.created_at,
                ),
            )
            await db.commit()

    async def get_document(
        self, doc_id: str, app_visible_only: bool = False
    ) -> KbDocument | None:
        """取单个文档。`app_visible_only` 下源内容被隐藏的文档返回 None（路由 404）。

        与内容详情同理：**404 而不是 403**，否则就成了「这个 doc_id 存在且被隐藏」
        的存在性预言机。
        """
        if app_visible_only:
            sql = (
                "SELECT d.* FROM kb_documents d"
                + _APP_VISIBLE_DOC_JOINS
                + " WHERE d.doc_id=? AND ("
                + _APP_VISIBLE_DOC_WHERE
                + ")"
            )
        else:
            sql = "SELECT d.* FROM kb_documents d WHERE d.doc_id=?"
        async with connect() as db:
            cur = await db.execute(sql, (doc_id,))
            row = await cur.fetchone()
            return _doc_from_row(dict(row)) if row else None

    async def get_document_by_source(self, source_type: str, source_id: str) -> KbDocument | None:
        async with connect() as db:
            cur = await db.execute(
                "SELECT * FROM kb_documents WHERE source_type=? AND source_id=?",
                (source_type, source_id),
            )
            row = await cur.fetchone()
            return _doc_from_row(dict(row)) if row else None

    async def get_ready_source_ids(
        self, source_type: str, source_ids: list[str]
    ) -> set[str]:
        """批量查「已真正入库（status=ready）」的来源 id，供列表页标 ✓已入库。

        只认 ready：pending/embedding/failed 都不是可检索状态，标成已入库会误导。
        """
        if not source_ids:
            return set()
        marks = ",".join("?" * len(source_ids))
        async with connect() as db:
            cur = await db.execute(
                f"SELECT source_id FROM kb_documents WHERE source_type=? "
                f"AND status='ready' AND source_id IN ({marks})",
                [source_type, *source_ids],
            )
            return {dict(r)["source_id"] for r in await cur.fetchall()}

    async def get_document_by_hash(self, content_hash: str) -> KbDocument | None:
        async with connect() as db:
            cur = await db.execute(
                "SELECT * FROM kb_documents WHERE content_hash=?", (content_hash,)
            )
            row = await cur.fetchone()
            return _doc_from_row(dict(row)) if row else None

    async def list_documents(
        self, limit: int = 50, offset: int = 0, app_visible_only: bool = False
    ) -> list[KbDocument]:
        """文档列表。`app_visible_only` 下源内容被隐藏（或源已不存在）的文档不返回。

        过滤在 **LIMIT 之前**下推：先分页再过滤会让 app 拿到不满一页的列表，
        按 limit 算出的总页数全错。manual 文档无源内容，不受影响。
        """
        if app_visible_only:
            sql = (
                "SELECT d.* FROM kb_documents d"
                + _APP_VISIBLE_DOC_JOINS
                + " WHERE"
                + _APP_VISIBLE_DOC_WHERE
                + " ORDER BY d.rowid DESC LIMIT ? OFFSET ?"
            )
        else:
            sql = "SELECT d.* FROM kb_documents d ORDER BY d.rowid DESC LIMIT ? OFFSET ?"
        async with connect() as db:
            cur = await db.execute(sql, (limit, offset))
            rows = await cur.fetchall()
            return [_doc_from_row(dict(r)) for r in rows]

    async def set_document_state(self, doc_id: str, status: str, chunk_count: int) -> None:
        async with connect() as db:
            await db.execute(
                "UPDATE kb_documents SET status=?, chunk_count=? WHERE doc_id=?",
                (status, chunk_count, doc_id),
            )
            await db.commit()

    async def add_chunks(self, chunks: list[KbChunk]) -> None:
        async with connect() as db:
            for c in chunks:
                await db.execute(
                    """INSERT INTO kb_chunks
                       (chunk_id, doc_id, chunk_index, text, embedding, modality,
                        image_url, meta, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        c.chunk_id, c.doc_id, c.chunk_index, c.text,
                        json.dumps(c.embedding, ensure_ascii=False),
                        c.modality, c.image_url,
                        json.dumps(c.meta, ensure_ascii=False), c.created_at,
                    ),
                )
            await db.commit()

    async def list_chunks_by_doc(self, doc_id: str) -> list[KbChunk]:
        async with connect() as db:
            cur = await db.execute(
                "SELECT * FROM kb_chunks WHERE doc_id=? ORDER BY chunk_index", (doc_id,)
            )
            rows = await cur.fetchall()
            return [_chunk_from_row(dict(r)) for r in rows]

    async def iter_all_chunks(self) -> list[KbChunk]:
        async with connect() as db:
            cur = await db.execute("SELECT * FROM kb_chunks ORDER BY rowid")
            rows = await cur.fetchall()
            return [_chunk_from_row(dict(r)) for r in rows]

    async def iter_retrievable_chunks(self, app_visible_only: bool = False) -> list[KbChunk]:
        """可检索 chunk —— 红线的**行为层**防线。

        未审核通过的内容（及其分析产物）产生的 chunk 不返回，因此即便数据里
        混进了越线行，检索也命中不到、更不会被引用作答。manual 文档是人工录入的
        终态，直接放行。

        **`app_visible_only` 是叠加在这条红线上的第二层，不是它的替代**：
        app 端被隐藏的内容，其 chunk 在 app 通道下同样不返回。检索与问答共用这
        一个方法（`KbService.search()` 是唯一入口，`/kb/search` 与 `/kb/ask` 都走
        它），一处过滤同时覆盖两条路径 —— 不存在「检索滤了、问答没滤」的漏。

        三值逻辑提醒：`c1.app_hidden_at IS NULL` 在 c1 整行为 NULL（悬空来源）时
        求值为 TRUE，看着像"放行"。真正拦住它的是**同一 AND 组**里的
        `c1.review_status = 'approved'`（对 NULL 不成立）。这两个条件绝不能被拆到
        外层 OR 去 —— 拆开就是击穿红线。所以这里选择在既有 AND 组里追加条件，
        而不是把整个 WHERE 重写成 EXISTS 形式。

        干净数据下本查询与 iter_all_chunks 的结果集完全一致（同一 rowid 排序），
        排序不变量不受影响。
        """
        if app_visible_only:
            f1 = " AND c1.app_hidden_at IS NULL"
            f2 = " AND c2.app_hidden_at IS NULL"
        else:
            f1 = f2 = ""
        async with connect() as db:
            cur = await db.execute(
                f"""
                SELECT k.* FROM kb_chunks k
                JOIN kb_documents d ON k.doc_id = d.doc_id
                LEFT JOIN contents c1
                       ON d.source_type = 'content' AND d.source_id = c1.content_id
                LEFT JOIN analyses a
                       ON d.source_type = 'analysis' AND d.source_id = a.analysis_id
                LEFT JOIN contents c2 ON a.content_id = c2.content_id
                WHERE d.source_type = 'manual'
                   OR (d.source_type = 'content'  AND c1.review_status = 'approved'{f1})
                   OR (d.source_type = 'analysis' AND c2.review_status = 'approved'{f2})
                ORDER BY k.rowid
                """
            )
            rows = await cur.fetchall()
            return [_chunk_from_row(dict(r)) for r in rows]

    async def delete_chunks_by_doc(self, doc_id: str) -> None:
        """重新向量化前全量重建（不做增量 patch）。"""
        async with connect() as db:
            await db.execute("DELETE FROM kb_chunks WHERE doc_id=?", (doc_id,))
            await db.commit()

    async def update_document_body(
        self, doc_id: str, title: str, markdown: str, raw_text: str, content_hash: str
    ) -> None:
        """重蒸馏时替换文档正文。刻意不动 status/chunk_count（是否作废既有向量由 service 决定）。"""
        async with connect() as db:
            await db.execute(
                """UPDATE kb_documents SET title=?, markdown=?, raw_text=?, content_hash=?
                   WHERE doc_id=?""",
                (title, markdown, raw_text, content_hash, doc_id),
            )
            await db.commit()

    async def delete_document(self, doc_id: str) -> None:
        async with connect() as db:
            await db.execute("DELETE FROM kb_chunks WHERE doc_id=?", (doc_id,))
            await db.execute("DELETE FROM kb_qa_pairs WHERE doc_id=?", (doc_id,))
            await db.execute("DELETE FROM kb_documents WHERE doc_id=?", (doc_id,))
            await db.commit()

    # ---------- Q&A 知识条目 ----------

    async def add_qa_pairs(self, pairs: list[KbQaPair]) -> None:
        if not pairs:
            return
        async with connect() as db:
            for p in pairs:
                await db.execute(
                    """INSERT INTO kb_qa_pairs
                       (qa_id, doc_id, qa_index, question, answer, dimensions, evidence, tags,
                        source_type, source_id, source_url, source_author, status,
                        created_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        p.qa_id, p.doc_id, p.qa_index, p.question, p.answer,
                        json.dumps(p.dimensions, ensure_ascii=False),
                        json.dumps(p.evidence, ensure_ascii=False),
                        json.dumps(p.tags, ensure_ascii=False),
                        p.source_type, p.source_id, p.source_url, p.source_author,
                        p.status, p.created_at, p.updated_at,
                    ),
                )
            await db.commit()

    async def get_qa_pair(self, qa_id: str) -> KbQaPair | None:
        async with connect() as db:
            cur = await db.execute("SELECT * FROM kb_qa_pairs WHERE qa_id=?", (qa_id,))
            row = await cur.fetchone()
            return _qa_from_row(dict(row)) if row else None

    async def list_qa_pairs(
        self, doc_id: str, app_visible_only: bool = False
    ) -> list[KbQaPair]:
        """文档下的问答对。`app_visible_only` 下若**该文档**在 app 端不可见，整批不返回。

        判定依据是 **doc 的可见性，不是 pair 自己的 `source_id`** —— pair 上的
        `source_type` 恒为 'distilled'，`source_id` 可能是 analysis_id 也可能是
        content_id（取决于蒸馏入口），单看 pair 分不清该去接哪张表。doc 上
        `source_type` / `source_id` 才是明确成对、与可见性同源的。判一次覆盖全批。
        """
        if app_visible_only:
            sql = (
                "SELECT q.* FROM kb_qa_pairs q JOIN kb_documents d ON q.doc_id = d.doc_id"
                + _APP_VISIBLE_DOC_JOINS
                + " WHERE q.doc_id=? AND ("
                + _APP_VISIBLE_DOC_WHERE
                + ") ORDER BY q.qa_index"
            )
        else:
            sql = "SELECT q.* FROM kb_qa_pairs q WHERE q.doc_id=? ORDER BY q.qa_index"
        async with connect() as db:
            cur = await db.execute(sql, (doc_id,))
            rows = await cur.fetchall()
            return [_qa_from_row(dict(r)) for r in rows]

    async def list_qa_pairs_by_status(
        self, status: str, limit: int = 50, offset: int = 0, app_visible_only: bool = False
    ) -> list[KbQaPair]:
        """按状态列问答对（问答对审核页）。过滤同样下推到 LIMIT 之前。"""
        if app_visible_only:
            sql = (
                "SELECT q.* FROM kb_qa_pairs q JOIN kb_documents d ON q.doc_id = d.doc_id"
                + _APP_VISIBLE_DOC_JOINS
                + " WHERE q.status=? AND ("
                + _APP_VISIBLE_DOC_WHERE
                + ") ORDER BY q.rowid DESC LIMIT ? OFFSET ?"
            )
        else:
            sql = (
                "SELECT q.* FROM kb_qa_pairs q WHERE q.status=?"
                " ORDER BY q.rowid DESC LIMIT ? OFFSET ?"
            )
        async with connect() as db:
            cur = await db.execute(sql, (status, limit, offset))
            rows = await cur.fetchall()
            return [_qa_from_row(dict(r)) for r in rows]

    async def update_qa_pair(self, pair: KbQaPair) -> None:
        async with connect() as db:
            await db.execute(
                """UPDATE kb_qa_pairs SET qa_index=?, question=?, answer=?, dimensions=?,
                   evidence=?, tags=?, status=?, updated_at=? WHERE qa_id=?""",
                (
                    pair.qa_index, pair.question, pair.answer,
                    json.dumps(pair.dimensions, ensure_ascii=False),
                    json.dumps(pair.evidence, ensure_ascii=False),
                    json.dumps(pair.tags, ensure_ascii=False),
                    pair.status, pair.updated_at, pair.qa_id,
                ),
            )
            await db.commit()

    async def update_qa_pairs_status(self, qa_ids: list[str], status: str) -> int:
        """批量改审核状态，返回实际影响行数（供路由核对 requested vs changed）。"""
        if not qa_ids:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        async with connect() as db:
            cur = await db.execute(
                f"""UPDATE kb_qa_pairs SET status=?, updated_at=?
                    WHERE qa_id IN ({_placeholders(len(qa_ids))})""",
                (status, now, *qa_ids),
            )
            await db.commit()
            return cur.rowcount or 0

    async def delete_qa_pair(self, qa_id: str) -> None:
        async with connect() as db:
            await db.execute("DELETE FROM kb_qa_pairs WHERE qa_id=?", (qa_id,))
            await db.commit()

    async def delete_qa_pairs_by_doc(self, doc_id: str, status: str | None = None) -> int:
        """重蒸馏时清理旧草稿；传 status 可只删某一状态（approved 永不自动删除）。"""
        async with connect() as db:
            if status is None:
                cur = await db.execute("DELETE FROM kb_qa_pairs WHERE doc_id=?", (doc_id,))
            else:
                cur = await db.execute(
                    "DELETE FROM kb_qa_pairs WHERE doc_id=? AND status=?", (doc_id, status)
                )
            await db.commit()
            return cur.rowcount or 0

    async def count_qa_pairs_by_doc(
        self, doc_ids: list[str], status: str | None = None
    ) -> dict[str, int]:
        """列表页富化用：一次查询取回多个文档的 pair 数，避免 N+1。status 为空则统计全部。"""
        if not doc_ids:
            return {}
        sql = (
            f"SELECT doc_id, COUNT(*) FROM kb_qa_pairs "
            f"WHERE doc_id IN ({_placeholders(len(doc_ids))})"
        )
        params: list[str] = list(doc_ids)
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " GROUP BY doc_id"
        async with connect() as db:
            cur = await db.execute(sql, tuple(params))
            return {r[0]: r[1] for r in await cur.fetchall()}
