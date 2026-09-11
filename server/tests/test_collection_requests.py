"""app 采集申请：提交意图（app 可） / 放行决策（仅管理员）。

**这个文件守的是一条边界，不是一组功能。** app 设备令牌编在 APK 里可被反编译，
所以它只能「申请」，不能「批准」：

    POST  /api/v1/collection-requests        app 令牌 201  —— 纯写库，零外呼
    GET   /api/v1/collection-requests        app 令牌 200
    PATCH /api/v1/collection-requests/{id}   app 令牌 401  ← 核心断言

实现靠 auth.py::_APP_TOKEN_DENY_PREFIXES 里那个**带尾斜杠**的前缀。若有人图省事
把尾斜杠删掉，`test_app_token_cannot_decide` 会立刻变红。
"""
from __future__ import annotations

import httpx
import pytest

from app.auth import COOKIE_NAME, create_session
from app.config import settings
from app.domain.enums import RequestStatus
from app.main import create_app
from app.repositories.collection_request import SqliteCollectionRequestRepository
from app.repositories.sqlite import SqliteRunRepository

APP_TOKEN = "app-token-for-test"
APP_HEADER = "X-Datapp-App-Token"

REQ_PATH = "/api/v1/collection-requests"


def _client(app, cookies=None):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", cookies=cookies
    )


def _admin_cookies():
    return {COOKIE_NAME: create_session(settings.admin_username)}


@pytest.fixture
def with_app_token(monkeypatch):
    monkeypatch.setattr(settings, "app_token", APP_TOKEN)


@pytest.fixture(autouse=True)
def _no_llm(monkeypatch):
    """采集路径不触模型，但保险起见把 key 摘掉，杜绝测试里的任何真实外发。"""
    monkeypatch.setattr(settings, "llm_api_key", None)


async def _submit(client, target="露营装备", max_items=5, headers=None):
    return await client.post(
        REQ_PATH,
        json={"target": target, "max_items": max_items},
        headers=headers or {APP_HEADER: APP_TOKEN},
    )


# ------------------------------------------------- app 通道：能申请，能看进度

async def test_app_token_can_submit_intent(init_test_db, make_adapter, with_app_token):
    """核心正向：app 能提交采集意图 —— 且此时**一个 run 都不该存在**（零外呼）。"""
    app = create_app(adapter=make_adapter(), auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            resp = await _submit(client)
        runs = await SqliteRunRepository().list()
    assert resp.status_code == 201, f"{resp.status_code} {resp.text}"
    body = resp.json()
    assert body["status"] == "pending"
    assert body["target"] == "露营装备"
    assert body["target_type"] == "keyword"
    assert body["run_id"] is None
    assert runs == [], "提交意图阶段就建了 run —— 采集被提前触发了"


async def test_app_token_can_list_requests(init_test_db, make_adapter, with_app_token):
    app = create_app(adapter=make_adapter(), auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            await _submit(client)
            resp = await client.get(REQ_PATH, headers={APP_HEADER: APP_TOKEN})
    assert resp.status_code == 200, f"{resp.status_code} {resp.text}"
    assert len(resp.json()) == 1


# -------------------------------------------- 核心：app 不得批准自己提交的申请

async def test_app_token_cannot_decide(init_test_db, make_adapter, with_app_token):
    """**本次最重要的断言。** 尾斜杠前缀一旦丢失，放行权就落到可被反编译的令牌手里。"""
    app = create_app(adapter=make_adapter(), auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            req_id = (await _submit(client)).json()["id"]
            approve = await client.patch(
                f"{REQ_PATH}/{req_id}",
                json={"action": "approve"},
                headers={APP_HEADER: APP_TOKEN},
            )
            reject = await client.patch(
                f"{REQ_PATH}/{req_id}",
                json={"action": "reject"},
                headers={APP_HEADER: APP_TOKEN},
            )
        runs = await SqliteRunRepository().list()
    assert approve.status_code == 401, f"app 令牌竟能放行采集: {approve.status_code}"
    assert reject.status_code == 401, f"app 令牌竟能驳回: {reject.status_code}"
    assert runs == [], "被拒的放行请求仍然建出了 run"


async def test_app_token_cannot_create_raw_run(init_test_db, make_adapter, with_app_token):
    """旧边界未被带松：绕过申请单直接打 /collection-runs 依然 401。"""
    app = create_app(adapter=make_adapter(), auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            resp = await client.post(
                "/api/v1/collection-runs",
                json={"target_type": "keyword", "target": "任意", "max_items": 5},
                headers={APP_HEADER: APP_TOKEN},
            )
        runs = await SqliteRunRepository().list()
    assert resp.status_code == 401
    assert runs == []


# ------------------------------------------------------------ 管理员：放行 / 驳回

async def test_admin_approve_creates_linked_run(init_test_db, make_adapter, with_app_token):
    """放行 = 建一个真实 run，并把两头串起来（run.request_id ↔ req.run_id）。"""
    app = create_app(adapter=make_adapter(), auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            req_id = (await _submit(client, target="露营装备", max_items=5)).json()["id"]
            async with _client(app, cookies=_admin_cookies()) as admin:
                resp = await admin.patch(f"{REQ_PATH}/{req_id}", json={"action": "approve"})
        req = await SqliteCollectionRequestRepository().get(req_id)
        runs = await SqliteRunRepository().list()

    assert resp.status_code == 200, f"{resp.status_code} {resp.text}"
    assert req.status is RequestStatus.APPROVED
    assert req.decided_at and req.decided_by
    assert len(runs) == 1
    assert runs[0].request_id == req_id, "run 没有回指申请单"
    assert req.run_id == runs[0].id, "申请单没有回指 run"
    assert runs[0].target == "露营装备"
    assert runs[0].max_items == 5


async def test_admin_reject_creates_no_run(init_test_db, make_adapter, with_app_token):
    app = create_app(adapter=make_adapter(), auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            req_id = (await _submit(client)).json()["id"]
            async with _client(app, cookies=_admin_cookies()) as admin:
                resp = await admin.patch(
                    f"{REQ_PATH}/{req_id}", json={"action": "reject", "note": "关键词太宽泛"}
                )
        req = await SqliteCollectionRequestRepository().get(req_id)
        runs = await SqliteRunRepository().list()

    assert resp.status_code == 200
    assert req.status is RequestStatus.REJECTED
    assert req.note == "关键词太宽泛"
    assert runs == [], "驳回的申请竟然触发了采集"


@pytest.mark.parametrize("action", ["approve", "reject"])
async def test_double_decide_conflict(
    init_test_db, make_adapter, with_app_token, action
):
    """重复决策 → 409。放行方向的重复尤其要命：会造出第二个真实采集任务。"""
    app = create_app(adapter=make_adapter(), auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            req_id = (await _submit(client)).json()["id"]
            async with _client(app, cookies=_admin_cookies()) as admin:
                first = await admin.patch(f"{REQ_PATH}/{req_id}", json={"action": "approve"})
                second = await admin.patch(f"{REQ_PATH}/{req_id}", json={"action": action})
        runs = await SqliteRunRepository().list()

    assert first.status_code == 200
    assert second.status_code == 409, f"重复决策未被拦: {second.status_code}"
    assert len(runs) == 1, f"重复放行造出了 {len(runs)} 个 run"


async def test_decide_missing_request_404(init_test_db, make_adapter, with_app_token):
    app = create_app(adapter=make_adapter(), auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app, cookies=_admin_cookies()) as admin:
            resp = await admin.patch(f"{REQ_PATH}/nope", json={"action": "approve"})
    assert resp.status_code == 404


# ------------------------------------------------------------------ 条数钳制

async def test_max_items_clamped_server_side(init_test_db, make_adapter, with_app_token):
    """AGENTS.md「单任务默认最多 10 条」—— 客户端要 100，服务端只给 10。"""
    app = create_app(adapter=make_adapter(), auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            resp = await _submit(client, max_items=100)
    assert resp.status_code == 201
    assert resp.json()["max_items"] == settings.collection_request_max_items == 10


# ------------------------------------------------------------------ 拒绝路径

async def test_missing_token_401(init_test_db, make_adapter, with_app_token):
    app = create_app(adapter=make_adapter(), auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            resp = await client.post(REQ_PATH, json={"target": "任意", "max_items": 5})
    assert resp.status_code == 401
