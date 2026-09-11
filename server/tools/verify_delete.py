"""真实链路验证：DELETE /contents/{id} 的级联清理。

刻意在**真库**上跑，因为删除是不可逆的，且要同时验证数据库行、报告数组摘除、
以及磁盘上封面文件真的消失——纯 offline 测试覆盖不到真实 schema 与真实路径。

造一条带完整下游的合成内容（断言/证据/人工判定/分析/报告/KB 文档与分块/raw_assets
+ 真实封面文件），经 HTTP 删除后逐表核对。报告里同时塞一条真实内容 id，
用来验证「摘除该 id 而非删整篇」。

用法：uv run python tools/verify_delete.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from app.config import settings

BASE = "http://127.0.0.1:8000/api/v1"
CID = "verify:delete-synthetic"
ANALYSIS_ID = "verify-analysis-0001"
DOC_CONTENT = "verify-doc-content"
DOC_ANALYSIS = "verify-doc-analysis"
REPORT_ID = "verify-report-mixed"
NOW = "2026-09-10T00:00:00+00:00"
FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str) -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {detail}")
    if not ok:
        FAILURES.append(label)


def http_delete(path: str) -> int:
    req = urllib.request.Request(f"{BASE}{path}", method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def main() -> int:
    conn = sqlite3.connect(settings.db_path)
    conn.execute("PRAGMA foreign_keys = ON")

    # 找一条真实内容，用来验证报告只摘除该 id
    real = conn.execute(
        "SELECT content_id FROM contents WHERE content_id NOT LIKE 'verify:%' LIMIT 1"
    ).fetchone()
    if real is None:
        print("库里没有真实内容，跳过报告摘除验证")
        real = ("placeholder",)
    real_id = real[0]

    cover = settings.media_dir / f"{uuid.uuid4().hex}.jpg"
    cover.parent.mkdir(parents=True, exist_ok=True)
    cover.write_bytes(b"\xff\xd8\xff\xe0 synthetic cover for delete verification")
    print(f"合成内容 {CID}")
    print(f"封面文件 {cover}（存在={cover.exists()}）")
    print(f"报告内保留的真实 id {real_id}\n")

    # ---------- 造数据 ----------
    conn.execute(
        "INSERT INTO contents (content_id, platform, platform_item_id, collected_at, title,"
        " review_status, cover_local, tags, active_batch_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (CID, "xhs", "synthetic", NOW, "验证用合成内容", "extracted",
         f"/media/{cover.name}", "[]", None),
    )
    conn.execute(
        "INSERT INTO analyses (analysis_id, content_id, verdict, payload, markdown,"
        " compared_with, created_at, schema_version, focus) VALUES (?,?,?,?,?,?,?,?,?)",
        (ANALYSIS_ID, CID, "viral", "{}", "# md", "[]", NOW, "1", ""),
    )
    claim_id = "verify-claim-0001"
    conn.execute(
        "INSERT INTO claims (claim_id, content_id, text, status, confidence, claim_type,"
        " meta, created_at, updated_at, batch_id, batch_seq) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (claim_id, CID, "合成断言", "supported", 0.9, "fact", "{}", NOW, NOW, "", 1),
    )
    conn.execute(
        "INSERT INTO evidence (evidence_id, claim_id, source_kind, excerpt, strength, collected_at)"
        " VALUES (?,?,?,?,?,?)",
        ("verify-ev-1", claim_id, "corpus", "excerpt", 0.5, NOW),
    )
    conn.execute(
        "INSERT INTO review_decisions (decision_id, claim_id, status, rationale, reviewer,"
        " evidence_ids, created_at) VALUES (?,?,?,?,?,?,?)",
        ("verify-dec-1", claim_id, "supported", "r", "human", "[]", NOW),
    )
    for doc_id, stype, sid in ((DOC_CONTENT, "content", CID), (DOC_ANALYSIS, "analysis", ANALYSIS_ID)):
        conn.execute(
            "INSERT INTO kb_documents (doc_id, source_type, source_id, title, tags, content_hash,"
            " status, chunk_count, created_at, raw_text, markdown, doc_type)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, stype, sid, "t", "[]", f"hash-{doc_id}", "ready", 1, NOW, "raw", "md", "general"),
        )
        conn.execute(
            "INSERT INTO kb_chunks (chunk_id, doc_id, chunk_index, text, embedding, modality, meta, created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (f"chunk-{doc_id}", doc_id, 0, "text", "[]", "text", "{}", NOW),
        )
    conn.execute(
        "INSERT INTO raw_assets (id, run_id, content_id, kind, mime, sha256, bytes, storage_path, collected_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        ("verify-asset-1", None, CID, "cover", "image/jpeg", "sha", 10, "p", NOW),
    )
    conn.execute(
        "INSERT INTO reports (report_id, title, content_ids, payload, markdown, created_at, schema_version)"
        " VALUES (?,?,?,?,?,?,?)",
        (REPORT_ID, "验证用混合报告", json.dumps([CID, real_id]), "{}", "# r", NOW, "1"),
    )
    conn.commit()

    before = _counts(conn)
    print("删除前各表命中行数:", before)
    print()

    # ---------- 删 ----------
    status = http_delete(f"/contents/{CID}")
    check("DELETE 返回 204", status == 204, f"HTTP {status}")

    after = _counts(conn)
    print("删除后各表命中行数:", after)
    print()
    for table, n in after.items():
        check(f"{table} 清零", n == 0, f"{before[table]} → {n}")

    # ---------- 报告摘除而非误删 ----------
    row = conn.execute("SELECT content_ids FROM reports WHERE report_id=?", (REPORT_ID,)).fetchone()
    if row is None:
        check("报告仍在（只摘除该 id）", False, "整篇报告被误删")
    else:
        ids = json.loads(row[0])
        check("报告仍在且已摘除该 id", CID not in ids, f"content_ids={ids}")
        check("报告保留其他内容 id", real_id in ids, f"real={real_id} 是否在列: {real_id in ids}")
        conn.execute("DELETE FROM reports WHERE report_id=?", (REPORT_ID,))
        conn.commit()

    # ---------- 磁盘文件 ----------
    check("封面文件已从磁盘删除", not cover.exists(), str(cover))

    # ---------- 未知 id ----------
    check("未知 content_id 返回 404", http_delete("/contents/verify:does-not-exist") == 404, "HTTP 404")

    conn.close()
    print()
    if FAILURES:
        print(f"[FAIL] {len(FAILURES)} checks failed:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("ALL CHECKS PASSED")
    return 0


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    q = {
        "claims": ("SELECT COUNT(*) FROM claims WHERE content_id=?", (CID,)),
        "evidence": ("SELECT COUNT(*) FROM evidence WHERE claim_id='verify-claim-0001'", ()),
        "review_decisions": ("SELECT COUNT(*) FROM review_decisions WHERE claim_id='verify-claim-0001'", ()),
        "analyses": ("SELECT COUNT(*) FROM analyses WHERE content_id=?", (CID,)),
        "kb_documents": (
            "SELECT COUNT(*) FROM kb_documents WHERE (source_type='content' AND source_id=?)"
            " OR (source_type='analysis' AND source_id=?)",
            (CID, ANALYSIS_ID),
        ),
        "kb_chunks": (
            "SELECT COUNT(*) FROM kb_chunks WHERE doc_id IN (?,?)",
            (DOC_CONTENT, DOC_ANALYSIS),
        ),
        "raw_assets": ("SELECT COUNT(*) FROM raw_assets WHERE content_id=?", (CID,)),
        "contents": ("SELECT COUNT(*) FROM contents WHERE content_id=?", (CID,)),
    }
    return {name: conn.execute(sql, params).fetchone()[0] for name, (sql, params) in q.items()}


if __name__ == "__main__":
    raise SystemExit(main())
