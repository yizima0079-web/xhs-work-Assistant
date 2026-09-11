"""SQLite 初始化与连接。单进程低频场景：每个仓储方法用短连接，配合 WAL 避免写锁冲突。"""
from __future__ import annotations

import re
from contextlib import asynccontextmanager
from pathlib import Path

import aiosqlite

from app.config import settings


def _schema_sql() -> str:
    # db.py 位于 server/app/db.py -> 上两级 = server/schema.sql
    schema = Path(__file__).resolve().parent.parent / "schema.sql"
    return schema.read_text(encoding="utf-8")


# 现有库幂等补列（CREATE TABLE IF NOT EXISTS 不会给已建表加列）。
# 所有迁移列必须带 DEFAULT / 可空，SQLite 才允许 ALTER TABLE ADD COLUMN。
_COLUMN_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "collection_runs": [
        # 手册 §4.3 的运行指标。老库补列后历史行为 NULL —— 不猜、不回填假数据。
        ("latency_ms", "INTEGER"),
    ],
    "claims": [
        ("claim_type", "TEXT"),
        ("meta", "TEXT NOT NULL DEFAULT '{}'"),
        # 审核版本化：每次重审新增一批，旧批次保留可回看（旧行回落为 batch_seq=1 的第 1 版）
        ("batch_id", "TEXT NOT NULL DEFAULT ''"),
        ("batch_seq", "INTEGER NOT NULL DEFAULT 1"),
    ],
    "contents": [
        ("cover_url", "TEXT"),
        ("cover_local", "TEXT"),
        ("tags", "TEXT"),
        # 生效审核批次；NULL → 读取时回落为 batch_seq 最大的一批
        ("active_batch_id", "TEXT"),
        # app 端软隐藏：NULL = 正常可见；非空 = 该 ISO 时刻起对 app 通道隐藏。
        # web（管理员 Cookie）通道照常可见并可恢复；老库补列后历史行天然全部可见，无需回填。
        ("app_hidden_at", "TEXT"),
    ],
    "kb_documents": [
        ("raw_text", "TEXT NOT NULL DEFAULT ''"),
        ("markdown", "TEXT NOT NULL DEFAULT ''"),
        # Q&A 知识库：旧库全部落回 general（markdown 切片），新表 kb_qa_pairs 见 schema.sql
        ("doc_type", "TEXT NOT NULL DEFAULT 'general'"),
    ],
    "analyses": [
        # 重新分析时用户注入的补充视角（"" = 标准分析）
        ("focus", "TEXT NOT NULL DEFAULT ''"),
    ],
}


# 建立在迁移列上的索引：老库要等 ALTER 补完列才建得出来，因此必须排在 _ensure_columns 之后。
# 放进 schema.sql 会在 executescript 阶段就报 "no such column"（老库启动直接挂）。
_POST_MIGRATION_DDL = [
    # claims：复合索引一次覆盖「按 content_id 取批次」「按 (content_id, batch_id) 取行」
    # 「MAX(batch_seq)」。老库残留的两个窄索引显式 DROP —— 重复索引只拖慢写入。
    "DROP INDEX IF EXISTS idx_claims_content",
    "DROP INDEX IF EXISTS idx_claims_batch",
    "CREATE INDEX IF NOT EXISTS idx_claims_content_batch "
    "ON claims(content_id, batch_id, batch_seq)",
    # contents.review_status 是看板最常用筛选列；schema.sql 里也有一条，
    # 这里兜底是为了「schema.sql 与迁移脚本各自都能独立跑通」。
    "CREATE INDEX IF NOT EXISTS idx_contents_review_status ON contents(review_status)",
]

# 老库遗留的**列默认值**漂移。SQLite 不支持 `ALTER TABLE ... ALTER COLUMN`，
# 改默认值只能整表重建，所以这里单独走一条「重建迁移」：只在真库默认值与
# schema.sql 不一致时触发，正常库零开销、老库只跑一次。
#
# 目前只有一处：kb_documents.status 早期真库建成了 DEFAULT 'embedding'。
# 但 status 的生命周期是 pending → embedding → ready → failed，默认必须是
# pending；否则任何省略 status 的 INSERT 都会把文档永久钉在「向量化中」——
# 既不会被 pending 扫描捡起来，也永远不会进 ready。
_DEFAULT_DRIFT_REPAIRS: list[tuple[str, str, str]] = [
    ("kb_documents", "status", "'pending'"),
]


def _create_table_ddl(table: str) -> str:
    """从 schema.sql 抠出该表的建表语句：重建表的 DDL 唯一来源，避免两处定义漂移。"""
    match = re.search(
        rf"CREATE TABLE IF NOT EXISTS {table}\s*\(.*?\n\);", _schema_sql(), re.S
    )
    if match is None:
        raise RuntimeError(f"schema.sql 里找不到 {table} 的建表语句")
    return match.group(0)


def _index_sql() -> str:
    """schema.sql 里所有单行 CREATE INDEX：重建表后用它把索引补回来。"""
    return "\n".join(
        line for line in _schema_sql().splitlines() if line.startswith("CREATE INDEX")
    )


async def _repair_column_defaults(db: aiosqlite.Connection) -> None:
    for table, column, expected in _DEFAULT_DRIFT_REPAIRS:
        cur = await db.execute(f"PRAGMA table_info({table})")
        info = {row[1]: row for row in await cur.fetchall()}
        if column not in info or (info[column][4] or "") == expected:
            continue
        await _rebuild_table(db, table)
        # 旧表被 DROP 会连带删掉它身上的索引，重建完补回来。
        await db.executescript(_index_sql())


async def _rebuild_table(db: aiosqlite.Connection, table: str) -> None:
    """12-step 重建的精简版：建影子表 → 拷同名列 → 换名。

    调用方保证此刻表已存在、列已由 _ensure_columns 补齐对齐。
    init_db 自己的连接没开 foreign_keys，DROP 父表不会牵连子表。
    """
    staging = f"{table}__rebuild"
    ddl = _create_table_ddl(table).replace(
        f"CREATE TABLE IF NOT EXISTS {table}", f"CREATE TABLE {staging}", 1
    )
    cur = await db.execute(f"PRAGMA table_info({table})")
    old_columns = {row[1] for row in await cur.fetchall()}

    await db.execute(f"DROP TABLE IF EXISTS {staging}")
    await db.execute(ddl)
    cur = await db.execute(f"PRAGMA table_info({staging})")
    new_columns = [row[1] for row in await cur.fetchall()]
    shared = ", ".join(name for name in new_columns if name in old_columns)

    await db.execute(f"INSERT INTO {staging} ({shared}) SELECT {shared} FROM {table}")
    await db.execute(f"DROP TABLE {table}")
    await db.execute(f"ALTER TABLE {staging} RENAME TO {table}")


async def _ensure_columns(db: aiosqlite.Connection) -> None:
    """对已存在的表做差量 ALTER TABLE ADD COLUMN，幂等。"""
    for table, columns in _COLUMN_MIGRATIONS.items():
        cur = await db.execute(f"PRAGMA table_info({table})")
        existing = {row[1] for row in await cur.fetchall()}
        for name, declaration in columns:
            if name not in existing:
                await db.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {declaration}"
                )


async def init_db() -> None:
    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(settings.db_path) as db:
        await db.execute("PRAGMA journal_mode = WAL")
        await db.executescript(_schema_sql())
        await _ensure_columns(db)
        for statement in _POST_MIGRATION_DDL:
            await db.execute(statement)
        # 最后一步：老库遗留的列默认值漂移（SQLite 改不了默认值，只能重建表）。
        await _repair_column_defaults(db)
        await db.commit()


@asynccontextmanager
async def connect():
    """打开一个短连接（WAL 由 init_db 设置，这里只设会话级 pragma）。"""
    conn = await aiosqlite.connect(settings.db_path)
    try:
        await conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = aiosqlite.Row
        yield conn
    finally:
        await conn.close()
