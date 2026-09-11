"""真实链路验证：app 端「删除」= 软隐藏的双通道可见性。

刻意在**真库**上跑 —— 要同时验证同一份数据在 app 通道收窄、在 web 通道保持，
纯 offline 测试用的是构造库，覆盖不到真库的派生数据分布（哪些内容真有 KB 文档 /
报告 / 断言），也覆盖不到真实服务进程里的通道判定中间件。

选一条派生数据最多的内容做目标，隐藏后逐条核对：app 通道看不到它（含派生数据、
概览计数），web 通道照常看到且带 `app_hidden_at`，设备令牌不能取消隐藏，
管理员 Cookie 取消后原子恢复。**结束时保证把目标恢复原状**（`finally` 里兜底），
不留任何 `app_hidden_at`。

用法（服务需已在 127.0.0.1:8000 运行）：
    uv run python tools/verify_app_hidden_e2e.py

**不打印任何密钥** —— 令牌只用于请求头，不进日志。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import COOKIE_NAME, create_session  # noqa: E402
from app.config import settings  # noqa: E402

BASE = "http://127.0.0.1:8000/api/v1"
PASS: list[str] = []
FAIL: list[str] = []

# 显式禁用代理：Windows 上 urllib 会读注册表里的系统代理，本机回环请求被塞给
# 上游代理就是 30s 超时（httpx 只读环境变量，所以先前没暴露这个坑）。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  |  {detail}" if detail else ""))


def ping() -> int:
    """根路径 `/health` 的存活探测 —— **刻意不用 `/api/v1/health`**。

    那个端点冷缓存时会 `spawn` 一个 OpenCLI 子进程做真实登录态探测（见
    `routes/health.py` 的 docstring），实测冷缓存 8.9s、偶发 >30s。拿它当存活探针，
    会把「服务好得很、只是探针慢」误判成「服务挂了」。这里只要一个纯内存 200。
    """
    try:
        with _OPENER.open("http://127.0.0.1:8000/health", timeout=10) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def call(method: str, path: str, headers: dict, params: dict | None = None):
    """返回 (status, 解析后的 body 或 None)。4xx/5xx 不抛，交给调用方断言。"""
    url = BASE + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, method=method, headers=headers)
    try:
        with _OPENER.open(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, None


def main() -> int:
    if not settings.app_token:
        print("DATAPP_APP_TOKEN 未配置，无法验证 app 通道")
        return 2
    app_h = {"X-Datapp-App-Token": settings.app_token}
    web_h = {"Cookie": f"{COOKIE_NAME}={create_session(settings.admin_username)}"}

    # 0) 服务存活
    st = ping()
    check("服务存活 /health 200", st == 200, str(st))
    if st != 200:
        return 2

    # 1) 基线：无隐藏项时两条通道看到同一个集合
    st_a, app0 = call("GET", "/contents", app_h, {"limit": 200})
    st_w, web0 = call("GET", "/contents", web_h, {"limit": 200})
    check("app 列表 200 / web 列表 200", st_a == 200 and st_w == 200, f"{st_a}/{st_w}")
    residual = [x for x in web0 if x.get("app_hidden_at")]
    check(
        "基线：库中无残留隐藏项",
        not residual,
        f"残留 {len(residual)} 条，已清理" if residual else f"共 {len(web0)} 条",
    )
    for x in residual:  # 上一次跑挂了留下的，先清干净
        call("DELETE", f"/contents/{x['content_id']}/app-hidden", web_h)
    if residual:
        _, app0 = call("GET", "/contents", app_h, {"limit": 200})
        _, web0 = call("GET", "/contents", web_h, {"limit": 200})
    app_ids0 = [x["content_id"] for x in app0]
    web_ids0 = [x["content_id"] for x in web0]
    check("基线：两通道集合一致", set(app_ids0) == set(web_ids0), f"app={len(app_ids0)} web={len(web_ids0)}")

    # 2) 选目标：挑派生数据最多的那条，覆盖最广
    _, summaries = call("GET", "/contents/summary", web_h, {"limit": 200})
    smap = {s["content_id"]: s for s in summaries}
    keys = ("analysis_count", "report_count", "claim_count", "kb_doc_count")
    target = max(web_ids0, key=lambda cid: sum(smap.get(cid, {}).get(k, 0) or 0 for k in keys))
    t = smap.get(target, {})
    title_target = next((x.get("title") or "" for x in web0 if x["content_id"] == target), "")
    print(
        f"      目标 {target}："
        + " ".join(f"{k.removesuffix('_count')}={t.get(k)}" for k in keys)
    )

    # 目标派生的 KB 文档 id（验证「派生数据一起隐藏」）
    _, docs = call("GET", "/kb/documents", web_h, {"limit": 200})
    doc_list = docs if isinstance(docs, list) else (docs or {}).get("items", [])
    target_docs = [
        d["doc_id"] for d in doc_list
        if d.get("source_type") == "content" and d.get("source_id") == target
    ]

    # 3) 隐藏（设备令牌 = app 端「删除」）
    st, _ = call("POST", f"/contents/{target}/app-hidden", app_h)
    check("app 令牌隐藏 → 204", st == 204, str(st))

    try:
        # 4) app 通道：列表不含、详情 404
        _, app1 = call("GET", "/contents", app_h, {"limit": 200})
        app_ids1 = [x["content_id"] for x in app1]
        check("app 列表 -1 条", len(app_ids1) == len(app_ids0) - 1, f"{len(app_ids0)} → {len(app_ids1)}")
        check("app 列表不含隐藏内容", target not in app_ids1)
        st, body = call("GET", f"/contents/{target}", app_h)
        check("app 详情 404（不是 403）", st == 404, str(st))
        # 判据是**标题**不是 id：404 的 detail 会回显请求方自己给的那个 id
        # （`内容不存在: {content_id}`），请求方本来就知道它，无信息增益。
        # 泄露的是「本来不知道的东西」—— 标题、正文、派生 id。
        check(
            "404 响应体不含标题",
            title_target not in json.dumps(body, ensure_ascii=False),
        )

        for path, label, want in (
            (f"/contents/{target}/claims", "claims", 404),
            (f"/contents/{target}/review-batches", "review-batches", 404),
            # 列表型子资源不校验父内容存在性，对「不存在」与「已隐藏」一律 200 + 空列表
            # —— 两者表现一致，同样不构成存在性预言机。详见开发日志。
            (f"/contents/{target}/analyses", "analyses", 200),
        ):
            st, body = call("GET", path, app_h)
            text = json.dumps(body, ensure_ascii=False)
            ok = st == want and title_target not in text
            check(f"app 通道 /{label} 不泄露隐藏内容", ok, f"{st}（期望 {want}）")

        # 5) app 通道：概览计数与 KB 文档不泄露
        _, app_sum = call("GET", "/contents/summary", app_h, {"limit": 200})
        check("app 概览不含隐藏内容", target not in {s["content_id"] for s in app_sum})
        if target_docs:
            _, app_docs = call("GET", "/kb/documents", app_h, {"limit": 200})
            ids = {d["doc_id"] for d in (app_docs if isinstance(app_docs, list) else app_docs.get("items", []))}
            check("app KB 文档列表不含其派生文档", not (set(target_docs) & ids), f"{len(target_docs)} 篇")

        # 6) web 通道：内容保持、带标记、详情 200
        _, web1 = call("GET", "/contents", web_h, {"limit": 200})
        web_ids1 = [x["content_id"] for x in web1]
        check("web 列表条数不变", len(web_ids1) == len(web_ids0), f"{len(web_ids0)} → {len(web_ids1)}")
        check("web 列表仍含该内容", target in web_ids1)
        row = next((x for x in web1 if x["content_id"] == target), {})
        check("web 该条带 app_hidden_at", bool(row.get("app_hidden_at")), str(row.get("app_hidden_at"))[:19])
        check("web 详情 200", call("GET", f"/contents/{target}", web_h)[0] == 200)

        # 7) 恢复权只归管理端
        st, _ = call("DELETE", f"/contents/{target}/app-hidden", app_h)
        check("app 令牌取消隐藏 → 401", st == 401, str(st))
        check("401 之后该内容对 app 仍不可见", call("GET", f"/contents/{target}", app_h)[0] == 404)

        # 8) web 恢复
        st, _ = call("DELETE", f"/contents/{target}/app-hidden", web_h)
        check("web 取消隐藏 → 204", st == 204, str(st))
    finally:
        # 中间哪步炸了都恢复原状，不留隐藏项
        call("DELETE", f"/contents/{target}/app-hidden", web_h)

    # 9) 终态
    _, app2 = call("GET", "/contents", app_h, {"limit": 200})
    app_ids2 = [x["content_id"] for x in app2]
    check("app 列表恢复原条数", len(app_ids2) == len(app_ids0), f"{len(app_ids1)} → {len(app_ids2)}")
    check("app 重新可见该内容", target in app_ids2)
    st, body = call("GET", f"/contents/{target}", app_h)
    check("app 详情恢复 200", st == 200, str(st))
    check("app 详情 app_hidden_at 已清空", not (body or {}).get("app_hidden_at"))

    _, web_final = call("GET", "/contents", web_h, {"limit": 200})
    check("终态：库中零隐藏项", not [x for x in web_final if x.get("app_hidden_at")])

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("失败项：" + "；".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
