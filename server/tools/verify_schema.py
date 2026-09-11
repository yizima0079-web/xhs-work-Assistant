"""真库结构与数据不变量校验（app 端对接前的「体检」）。

两类检查：

A. **结构完整性** —— 用 schema.sql 现建一个空白参考库，逐表逐列对比真库的
   「列名 / 类型 / NOT NULL / 默认值 / 主键序」。这能抓住 `CREATE TABLE IF NOT EXISTS`
   的固有缺陷：它**永远不会更新已存在表的列定义**，所以真库的默认值可能悄悄停留在
   某个历史版本上（真库的 kb_documents.status 曾漂移成 'embedding'，
   init_db 的 `_DEFAULT_DRIFT_REPAIRS` 会重建表修掉它，这段校验就是它的守门人）。
   同时核对索引齐全、`integrity_check`、`foreign_key_check`。

B. **数据不变量** —— 孤儿行、JSON 列可解析、枚举值合法、审核批次恰好一个生效、
   chunk_count 与实际分块数一致、未审核内容是否混进 kb_chunks（红线）。

只读：全程不开写事务。任何 FAIL → 非零退出。
用法：uv run python tools/verify_schema.py
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from app.config import settings

FAILURES: list[str] = []
WARNINGS: list[str] = []

EXPECTED_TABLES = [
    "analyses", "claims", "collection_events", "collection_runs", "contents",
    "evidence", "kb_chunks", "kb_documents", "kb_qa_pairs", "raw_assets",
    "reports", "review_decisions", "collection_requests",
]

# 枚举白名单（与 domain/enums.py、模型注释对齐）
ENUMS = {
    "contents.review_status": {"pending", "extracted", "approved", "rejected", "blocked"},
    "claims.status": {"unverified", "supported", "contradicted", "unclear"},
    "analyses.verdict": {"viral", "flat", "uncertain"},
    "kb_documents.status": {"pending", "embedding", "ready", "failed"},
    "kb_documents.source_type": {"analysis", "manual", "content"},
    "kb_documents.doc_type": {"general", "qa"},
    "kb_qa_pairs.status": {"draft", "approved", "rejected"},
    "kb_qa_pairs.source_type": {"distilled", "manual"},
    "collection_runs.status": {
        "queued", "running", "success", "partial", "blocked", "failed",
        "cancelled", "retry_wait",
    },
}

# JSON 文本列：(表, 列) —— 必须能 json.loads，且顶层类型符合预期
JSON_COLUMNS: list[tuple[str, str, type]] = [
    ("contents", "tags", list), ("contents", "media", list),
    ("contents", "engagement", dict), ("contents", "raw_refs", list),
    ("claims", "meta", dict),
    ("analyses", "payload", dict), ("analyses", "compared_with", list),
    ("kb_documents", "tags", list),
    ("kb_chunks", "meta", dict), ("kb_chunks", "embedding", list),
    ("kb_qa_pairs", "dimensions", dict), ("kb_qa_pairs", "evidence", list),
    ("kb_qa_pairs", "tags", list),
    ("reports", "content_ids", list), ("reports", "payload", dict),
]

# 孤儿检查：(描述, SQL，返回违规行数)
# 已知存量的红线越线文档：用户拍板**保留待复核**，不擅自删数据，因此转 WARN。
# 不在基线内的越线行仍然 FAIL —— 豁免是按 doc_id 点名的，不会掩盖新问题。
#
# 基线放在同目录的 redline_baseline.json，**不硬编码在这里**：这些 id 是真库特有
# 的，写死在代码里会让工具在其他库副本上把别人的正常文档误判成「已豁免」。
# 基线文件缺失 → 视为空基线（全部 FAIL），安全默认。
_BASELINE_PATH = Path(__file__).with_name("redline_baseline.json")


def load_redline_baseline() -> set[str]:
    """读取已知越线文档基线。文件不存在或损坏 → 空集合（宁可误报，不可漏报）。"""
    try:
        data = json.loads(_BASELINE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return set(data.get("known_violations", []))

# 与 app/repositories/kb.py::iter_retrievable_chunks 的放行条件必须逐字一致。
# 改动任一处都要同步另一处，否则本项检查会失去意义。
_RETRIEVABLE_JOINS = """
    LEFT JOIN contents c1 ON d.source_type='content'  AND d.source_id = c1.content_id
    LEFT JOIN analyses a  ON d.source_type='analysis' AND d.source_id = a.analysis_id
    LEFT JOIN contents c2 ON a.content_id = c2.content_id
"""
_RETRIEVABLE_PREDICATE = """
    d.source_type='manual'
 OR (d.source_type='content'  AND c1.review_status='approved')
 OR (d.source_type='analysis' AND c2.review_status='approved')
"""

ORPHANS: list[tuple[str, str]] = [
    ("claims.content_id → contents", "SELECT COUNT(*) FROM claims c LEFT JOIN contents t ON c.content_id=t.content_id WHERE t.content_id IS NULL"),
    ("evidence.claim_id → claims", "SELECT COUNT(*) FROM evidence e LEFT JOIN claims c ON e.claim_id=c.claim_id WHERE c.claim_id IS NULL"),
    ("review_decisions.claim_id → claims", "SELECT COUNT(*) FROM review_decisions d LEFT JOIN claims c ON d.claim_id=c.claim_id WHERE c.claim_id IS NULL"),
    ("analyses.content_id → contents", "SELECT COUNT(*) FROM analyses a LEFT JOIN contents t ON a.content_id=t.content_id WHERE t.content_id IS NULL"),
    ("kb_chunks.doc_id → kb_documents", "SELECT COUNT(*) FROM kb_chunks k LEFT JOIN kb_documents d ON k.doc_id=d.doc_id WHERE d.doc_id IS NULL"),
    ("kb_qa_pairs.doc_id → kb_documents", "SELECT COUNT(*) FROM kb_qa_pairs q LEFT JOIN kb_documents d ON q.doc_id=d.doc_id WHERE d.doc_id IS NULL"),
    ("collection_events.run_id → collection_runs", "SELECT COUNT(*) FROM collection_events e LEFT JOIN collection_runs r ON e.run_id=r.id WHERE r.id IS NULL"),
]


def ok(label: str, detail: str = "") -> None:
    print(f"  [PASS] {label}{': ' + detail if detail else ''}")


def fail(label: str, detail: str) -> None:
    print(f"  [FAIL] {label}: {detail}")
    FAILURES.append(f"{label} — {detail}")


def warn(label: str, detail: str) -> None:
    print(f"  [WARN] {label}: {detail}")
    WARNINGS.append(f"{label} — {detail}")


def check(label: str, condition: bool, detail: str = "") -> None:
    ok(label, detail) if condition else fail(label, detail)


# ---------------------------------------------------------------- A. 结构

def build_reference_db() -> Path:
    """用 schema.sql + 迁移补列，在临时目录现建一个空白参考库。"""
    from app.db import init_db

    ref = Path(tempfile.mkdtemp(prefix="datapp-ref-")) / "ref.db"
    original = settings.db_path
    settings.db_path = ref
    try:
        asyncio.run(init_db())
    finally:
        settings.db_path = original
    return ref


def table_columns(conn: sqlite3.Connection, table: str) -> dict[str, tuple]:
    """{列名: (类型, notnull, 默认值, 主键序)}"""
    return {
        r[1]: (r[2], r[3], r[4], r[5])
        for r in conn.execute(f'PRAGMA table_info("{table}")')
    }


def check_structure(live: sqlite3.Connection, ref: sqlite3.Connection) -> None:
    print("A. 结构完整性")

    have = {r[0] for r in live.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )}
    missing = [t for t in EXPECTED_TABLES if t not in have]
    check(f"{len(EXPECTED_TABLES)} 张业务表齐全", not missing,
          f"缺失 {missing}" if missing else f"实有 {len(EXPECTED_TABLES)} 张")
    extra = sorted(have - set(EXPECTED_TABLES))
    if extra:
        warn("真库存在契约外的表", ", ".join(extra))

    # 逐表逐列对比真库 vs schema.sql 新建库
    drifted = 0
    for table in EXPECTED_TABLES:
        lc, rc = table_columns(live, table), table_columns(ref, table)
        for name in sorted(set(lc) | set(rc)):
            if name not in rc:
                fail(f"{table}.{name}", f"真库有、schema.sql 无（疑似历史列）: {lc[name]}")
                drifted += 1
            elif name not in lc:
                fail(f"{table}.{name}", f"schema.sql 有、真库缺列: {rc[name]}")
                drifted += 1
            elif lc[name] != rc[name]:
                warn(f"{table}.{name} 定义漂移",
                     f"真库={lc[name]} vs schema.sql={rc[name]}（CREATE TABLE IF NOT EXISTS 不会更新已有表）")
                drifted += 1
    check("无列缺失 / 无多余列", not any("真库有、schema.sql 无" in f or "真库缺列" in f for f in FAILURES),
          f"漂移 {drifted} 处（详见上方 WARN）" if drifted else "逐列一致")

    # 索引
    def indexes(conn):
        return {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
        )}
    li, ri = indexes(live), indexes(ref)
    check("索引与 schema.sql 一致", li == ri,
          f"真库缺 {sorted(ri - li)} / 真库多 {sorted(li - ri)}" if li != ri
          else f"{len(li)} 个索引")
    check("idx_claims_content_batch 已建（迁移列上的索引）", "idx_claims_content_batch" in li,
          "老库升级路径正常")

    print()
    print("B. 数据库自身一致性")
    check("PRAGMA integrity_check = ok",
          live.execute("PRAGMA integrity_check").fetchone()[0] == "ok",
          live.execute("PRAGMA integrity_check").fetchone()[0])
    fk_violations = live.execute("PRAGMA foreign_key_check").fetchall()
    check("PRAGMA foreign_key_check 无违规", not fk_violations,
          f"{len(fk_violations)} 条 {fk_violations[:3]}" if fk_violations else "干净")
    check("journal_mode = wal", live.execute("PRAGMA journal_mode").fetchone()[0] == "wal",
          live.execute("PRAGMA journal_mode").fetchone()[0])
    # foreign_keys 是**逐连接**开关，SQLite 默认 0 —— 这个只读连接读到 0 属正常，
    # 真正有意义的是「app 自己的连接有没有开」。直接开一条 app 连接读它，
    # 把原先无条件的提醒换成真检查：开了才 PASS。
    from app.db import connect as app_connect

    async def _app_foreign_keys() -> int:
        async with app_connect() as conn:
            cur = await conn.execute("PRAGMA foreign_keys")
            row = await cur.fetchone()
            return int(row[0])

    check("app 连接已开启 PRAGMA foreign_keys", asyncio.run(_app_foreign_keys()) == 1,
          "app.db.connect() 每次连库都执行 PRAGMA foreign_keys = ON")


# ------------------------------------------------------------ B. 数据不变量

def check_invariants(live: sqlite3.Connection) -> None:
    print()
    print("C. 数据不变量")

    for label, sql in ORPHANS:
        n = live.execute(sql).fetchone()[0]
        check(f"无孤儿 {label}", n == 0, f"{n} 行孤儿" if n else "0")

    # JSON 列
    bad_json: list[str] = []
    for table, column, want in JSON_COLUMNS:
        for rowid, raw in live.execute(f'SELECT rowid, "{column}" FROM "{table}"'):
            if raw is None or raw == "":
                continue
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError) as exc:
                bad_json.append(f"{table}.{column} rowid={rowid}: {exc}")
                continue
            if not isinstance(parsed, want):
                bad_json.append(
                    f"{table}.{column} rowid={rowid}: 期望 {want.__name__}，实为 {type(parsed).__name__}"
                )
    check(f"{len(JSON_COLUMNS)} 个 JSON 列全部可解析且顶层类型正确", not bad_json,
          f"{len(bad_json)} 处异常：{bad_json[:3]}" if bad_json else "全部通过")

    # 枚举
    bad_enum: list[str] = []
    for spec, allowed in ENUMS.items():
        table, column = spec.split(".")
        rows = live.execute(
            f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL'
        ).fetchall()
        for (value,) in rows:
            if value not in allowed:
                bad_enum.append(f"{spec} = {value!r} 不在白名单")
    check(f"{len(ENUMS)} 个枚举列取值合法", not bad_enum,
          "; ".join(bad_enum[:3]) if bad_enum else "全部合法")

    # 审核批次：seq 合法 + 每个内容恰好一个生效批次 + active_batch_id 归属正确
    bad_seq = live.execute("SELECT COUNT(*) FROM claims WHERE batch_seq < 1").fetchone()[0]
    check("claims.batch_seq 全部 >= 1", bad_seq == 0, f"{bad_seq} 行非法")

    # 一个 batch_id 只能映射到一个 batch_seq。注意：一个内容**保留多个历史批次是正确的**
    # （版本化设计），别把它误判成串号。
    split_batch = live.execute(
        """
        SELECT content_id, batch_id, COUNT(DISTINCT batch_seq) AS n FROM claims
        GROUP BY content_id, batch_id HAVING n > 1
        """
    ).fetchall()
    check("batch_id <-> batch_seq 一对一（同批次不出现两个序号）", not split_batch,
          str([tuple(r) for r in split_batch][:3]) if split_batch else "0")

    batch_hist = live.execute(
        """
        SELECT batch_seq, COUNT(DISTINCT content_id) AS n_contents, COUNT(*) AS n_claims
        FROM claims GROUP BY batch_seq ORDER BY batch_seq
        """
    ).fetchall()
    ok("审核批次分布", "; ".join(f"第 {r[0]} 版: {r[1]} 内容 / {r[2]} 断言" for r in batch_hist) or "无")

    foreign_active = live.execute(
        """
        SELECT COUNT(*) FROM contents t WHERE t.active_batch_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM claims c
                          WHERE c.content_id = t.content_id AND c.batch_id = t.active_batch_id)
        """
    ).fetchone()[0]
    check("contents.active_batch_id 均属于该内容自己的批次", foreign_active == 0,
          f"{foreign_active} 条指向别处" if foreign_active else "0")

    n_contents = live.execute("SELECT COUNT(*) FROM contents").fetchone()[0]
    n_null_active = live.execute(
        "SELECT COUNT(*) FROM contents WHERE active_batch_id IS NULL"
    ).fetchone()[0]
    ok("生效批次回落统计",
       f"{n_contents} 条内容，其中 {n_null_active} 条 active_batch_id 为 NULL"
       f"（回落为 batch_seq 最大的一批 = 零回填设计）")

    # chunk_count 与实际分块数
    mismatch = live.execute(
        """
        SELECT COUNT(*) FROM kb_documents d
        WHERE d.chunk_count <> (SELECT COUNT(*) FROM kb_chunks k WHERE k.doc_id = d.doc_id)
        """
    ).fetchone()[0]
    check("kb_documents.chunk_count 与实际分块数一致", mismatch == 0,
          f"{mismatch} 篇不一致" if mismatch else "0")

    # 向量维度
    dims: dict[int, int] = {}
    unparsed = 0
    for (raw,) in live.execute("SELECT embedding FROM kb_chunks WHERE embedding NOT IN ('', '[]')"):
        try:
            dims[len(json.loads(raw))] = dims.get(len(json.loads(raw)), 0) + 1
        except (TypeError, ValueError):
            unparsed += 1
    check("已向量化 chunk 无解析失败", unparsed == 0, f"{unparsed} 条")
    if len(dims) > 1:
        warn("向量维度不统一", f"分布 {dims} —— 检索时余弦相似度会算错，必须同模型同维度")
    else:
        ok("向量维度统一", f"{dims or '暂无已向量化分块'}（config.embedding_dimension={settings.embedding_dimension}）")
    if dims and settings.embedding_dimension not in dims:
        warn("向量维度与配置不符",
             f"实维 {list(dims)} vs config.embedding_dimension={settings.embedding_dimension}")

    # 红线（数据层）：未审核内容不得进 kb_chunks。
    #
    # 判定方式是**检索视图的补集**，不是自己另写一遍 WHERE —— 旧写法只 JOIN
    # contents、只查 pending/blocked，于是整整漏掉了 analysis 型文档与
    # extracted/rejected/disputed 三种状态（真库上把 8 篇越线报成了 1 篇）。
    # 用 `_RETRIEVABLE_PREDICATE` 求补集，检查与被检查的对象天然同源：
    # 谓词改了，这里自动跟着改，不可能脱节。
    _HAS_VECTOR = "k.embedding NOT IN ('', '[]')"
    all_vectorized = {
        r[0] for r in live.execute(
            f"SELECT DISTINCT doc_id FROM kb_chunks k WHERE {_HAS_VECTOR}"
        )
    }
    retrievable = {
        r[0] for r in live.execute(
            f"""
            SELECT DISTINCT k.doc_id FROM kb_chunks k
            JOIN kb_documents d ON k.doc_id = d.doc_id
            {_RETRIEVABLE_JOINS}
            WHERE ({_RETRIEVABLE_PREDICATE}) AND {_HAS_VECTOR}
            """
        )
    }
    leaked_docs = sorted(all_vectorized - retrievable)

    known_set = load_redline_baseline()
    known = [d for d in leaked_docs if d in known_set]
    new_leaks = [d for d in leaked_docs if d not in known_set]
    if known:
        warn(f"红线：{len(known)} 篇存量越线文档（基线登记，保留待复核，未删除）",
             ", ".join(d[:12] for d in known))
    check("红线（数据层）：未审核内容未混进已向量化 kb_chunks", not new_leaks,
          f"{len(new_leaks)} 篇新越线 {[d[:12] for d in new_leaks][:3]}" if new_leaks
          else ("0（红线守住）" if not known else f"新增 0；另有 {len(known)} 篇存量"))

    # 红线（行为层）：即便数据层存在越线行，检索视图也必须把它们排除干净。
    # 这是「堵门禁、不删数据」方案的核心保证 —— 越线 chunk 物理上命中不到。
    for doc_id in leaked_docs:
        n = live.execute(
            f"""
            SELECT COUNT(*) FROM kb_chunks k
            JOIN kb_documents d ON k.doc_id = d.doc_id
            {_RETRIEVABLE_JOINS}
            WHERE k.doc_id = ? AND ({_RETRIEVABLE_PREDICATE})
            """,
            (doc_id,),
        ).fetchone()[0]
        check(f"红线（行为层）：越线文档 {doc_id[:12]} 的 chunk 不可被检索", n == 0,
              f"{n} 个 chunk 仍能进入 iter_retrievable_chunks")


def main() -> int:
    # Windows 终端默认 GBK，个别符号会让整个体检在 print 阶段崩掉（校验逻辑本身没跑完）。
    # 体检工具的职责是报问题，不是被输出编码绊倒 —— 无法编码的字符降级为 ?。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")

    print(f"真库 {settings.db_path}\n")
    live = sqlite3.connect(f"file:{settings.db_path}?mode=ro", uri=True)
    ref = sqlite3.connect(build_reference_db())
    try:
        check_structure(live, ref)
        check_invariants(live)
    finally:
        live.close()
        ref.close()

    print()
    if WARNINGS:
        print(f"[WARN] {len(WARNINGS)} 项非阻断提醒：")
        for w in WARNINGS:
            print("  -", w)
    if FAILURES:
        print(f"[FAIL] {len(FAILURES)} 项不通过：")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
