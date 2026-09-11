"""真实链路验证：审核批次版本化 / 分析多版本 + in_kb / 生效批次切换。

不造假：所有数字都从 HTTP 响应与 SQLite 现读现比，任何一条对不上就非零退出。
用法：uv run python tools/verify_versions.py <content_id>
"""
from __future__ import annotations

import json
import sqlite3
import sys
import urllib.error
import urllib.request

from app.config import settings

BASE = "http://127.0.0.1:8000/api/v1"
FAILURES: list[str] = []


def call(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=420) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:300]


def db():
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    return conn


def check(label: str, ok: bool, detail: str) -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: {detail}")
    if not ok:
        FAILURES.append(f"{label} — {detail}")


def main() -> int:
    cid = sys.argv[1]
    conn = db()
    print(f"内容 {cid}\n")

    # ---------- R2：分析多版本 + in_kb ----------
    print("R2 分析多版本 / 入库标记")
    _, before = call("GET", f"/contents/{cid}/analyses?limit=50")
    n0 = len(before)
    print(f"  现有 {n0} 版")

    status, created = call("POST", f"/contents/{cid}/analyze", {"focus": ""})
    check("新增一版分析返回 200", status == 200, f"HTTP {status}")
    if status != 200:
        return 1
    _, after = call("GET", f"/contents/{cid}/analyses?limit=50")
    check("版本数 +1（重析不覆盖）", len(after) == n0 + 1, f"{n0} → {len(after)}")
    check("倒序且首条为刚生成的", after[0]["analysis_id"] == created["analysis_id"],
          f"head={after[0]['analysis_id'][:8]} new={created['analysis_id'][:8]}")

    # 入库第 2 版（不是最新版）——正是用户描述的「第 2 版更好，存第 2 版」
    if len(after) >= 2:
        target = after[1]
        status, doc = call("POST", f"/kb/analyses/{target['analysis_id']}")
        check("第 2 版入库返回 200", status == 200, f"HTTP {status}")
        if status == 200:
            check("入库 source_id 指向第 2 版", doc.get("source_id") == target["analysis_id"],
                  f"source_id={str(doc.get('source_id'))[:8]}")
        _, marked = call("GET", f"/contents/{cid}/analyses?limit=50")
        flags = {a["analysis_id"]: a["in_kb"] for a in marked}
        check("只有第 2 版 in_kb=True",
              flags.get(target["analysis_id"]) is True
              and not any(v for k, v in flags.items() if k != target["analysis_id"]),
              f"flags={ {k[:8]: v for k, v in flags.items()} }")

    # ---------- R3：审核批次版本化 ----------
    print("\nR3 审核批次版本化")
    _, batches0 = call("GET", f"/contents/{cid}/review-batches")
    _, detail0 = call("GET", f"/contents/{cid}/claims")
    total0 = conn.execute("SELECT COUNT(*) FROM claims WHERE content_id=?", (cid,)).fetchone()[0]
    print(f"  现有 {len(batches0)} 批 / claims 总数 {total0}")

    for _ in range(2):
        status, _res = call("POST", f"/contents/{cid}/review?force=true")
        if status != 200:
            check("force 重审返回 200", False, f"HTTP {status} {_res}")
            return 1

    _, batches1 = call("GET", f"/contents/{cid}/review-batches")
    total1 = conn.execute("SELECT COUNT(*) FROM claims WHERE content_id=?", (cid,)).fetchone()[0]
    check("新增两个批次", len(batches1) == len(batches0) + 2, f"{len(batches0)} → {len(batches1)}")
    check("批次 seq 单调递增且倒序",
          [b["batch_seq"] for b in batches1] == sorted((b["batch_seq"] for b in batches1), reverse=True),
          str([b["batch_seq"] for b in batches1]))
    check("旧批次行未被删除（总数为各批次之和）",
          total1 == sum(b["claim_count"] for b in batches1) and total1 >= total0,
          f"总数 {total1} = Σ{[b['claim_count'] for b in batches1]}，迁移前 {total0}")
    check("恰好一个批次 active", sum(1 for b in batches1 if b["active"]) == 1,
          str([(b["batch_seq"], b["active"]) for b in batches1]))

    # 切到第 2 版（非最新）→ contents.review_status 必须随该批重算
    target_batch = sorted(batches1, key=lambda b: b["batch_seq"], reverse=True)[1]
    status, _ = call("POST", f"/contents/{cid}/review-batches/{target_batch['batch_id']}/activate")
    check("激活第 2 版返回 200", status == 200, f"HTTP {status}")
    row = conn.execute("SELECT review_status, active_batch_id FROM contents WHERE content_id=?", (cid,)).fetchone()
    check("active_batch_id 已落库", row["active_batch_id"] == target_batch["batch_id"],
          f"active_batch_id={str(row['active_batch_id'])[:8]}")
    _, d2 = call("GET", f"/contents/{cid}/claims")
    check("默认详情回落到生效批次", d2["batch_id"] == target_batch["batch_id"],
          f"batch_id={str(d2['batch_id'])[:8]} vs {target_batch['batch_id'][:8]}")
    check("详情断言数 = 该批次计数", len(d2["claims"]) == target_batch["claim_count"],
          f"{len(d2['claims'])} vs {target_batch['claim_count']}")
    _, d1 = call("GET", f"/contents/{cid}/claims?batch_id={batches1[-1]['batch_id']}")
    check("显式指定旧批次可取回旧断言", d1["batch_id"] == batches1[-1]["batch_id"],
          f"{len(d1['claims'])} 条")

    # ---------- R4：人工改判只影响本批次 ----------
    print("\nR4 人工改判的批次隔离")
    if d1["claims"]:
        claim = d1["claims"][0]["claim"]
        status_before = conn.execute("SELECT review_status FROM contents WHERE content_id=?", (cid,)).fetchone()[0]
        status, _ = call("POST", f"/claims/{claim['claim_id']}/decision",
                         {"status": "contradicted", "rationale": "验证用人工改判", "evidence_ids": []})
        check("改判旧批次断言返回 200", status == 200, f"HTTP {status}")
        status_after = conn.execute("SELECT review_status FROM contents WHERE content_id=?", (cid,)).fetchone()[0]
        check("生效版本的内容状态不被旧批次改动带偏", status_before == status_after,
              f"{status_before} → {status_after}")
        own = conn.execute("SELECT status FROM claims WHERE claim_id=?", (claim["claim_id"],)).fetchone()[0]
        check("该断言自身已改判", own == "contradicted", own)
        _, d2b = call("GET", f"/contents/{cid}/claims")
        other = [c["claim"]["claim_id"] for c in d2b["claims"] if c["claim"]["claim_id"] == claim["claim_id"]]
        check("生效批次里不出现被改判的旧批次断言", not other, f"overlap={other}")

    # ---------- R5：summary 与逐条查询一致 ----------
    print("\nR5 /contents/summary 一致性")
    _, summary = call("GET", "/contents/summary?limit=50")
    hit = next((s for s in summary if s["content_id"] == cid), None)
    if hit:
        n_analysis = conn.execute("SELECT COUNT(*) FROM analyses WHERE content_id=?", (cid,)).fetchone()[0]
        n_batch = conn.execute("SELECT COUNT(DISTINCT batch_id) FROM claims WHERE content_id=?", (cid,)).fetchone()[0]
        check("analysis_count 与库一致", hit["analysis_count"] == n_analysis, f"{hit['analysis_count']} vs {n_analysis}")
        check("review_batch_count 与库一致", hit["review_batch_count"] == n_batch, f"{hit['review_batch_count']} vs {n_batch}")
    else:
        check("summary 含该内容", False, "未找到")

    print()
    if FAILURES:
        print(f"[FAIL] {len(FAILURES)} checks failed:")
        for f in FAILURES:
            print("  -", f)
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
