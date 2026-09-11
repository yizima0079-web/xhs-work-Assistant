"""移动端设备令牌：app 用 X-Datapp-App-Token 免账号密码访问。

设计要点：令牌**可读可写**（app 要继承分析/审核/报告/向量化/删除），
但**不含采集模块** —— `/collection-runs` 与 `/browser/*` 一律 401。

为什么把采集摘出去：令牌是编进 APK 的（local.properties → BuildConfig），
反编译即可取出。拦掉采集就把「可被反编译」与「触发真实平台采集」隔开，
泄露后果限制在「改库里的数据」，守住「低频少量、不超出授权范围」那条红线。

未配置 Token 时该通道整体关闭（不会退化成「空 Token 放行」）。
"""
from __future__ import annotations

import httpx
import pytest

from app.config import settings
from app.domain.models import Content
from app.main import create_app
from app.repositories.sqlite import SqliteContentRepository

APP_TOKEN = "app-token-for-test"
APP_HEADER = "X-Datapp-App-Token"


def _client(app, cookies=None):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t", cookies=cookies
    )


async def _seed():
    await SqliteContentRepository().upsert(Content(
        content_id="xhs:a", platform="xhs", platform_item_id="a", text="正文",
    ))


@pytest.fixture
def with_app_token(monkeypatch):
    monkeypatch.setattr(settings, "app_token", APP_TOKEN)


@pytest.fixture
def without_app_token(monkeypatch):
    monkeypatch.setattr(settings, "app_token", None)


# ---------------------------------------------------------------- 放行读

@pytest.mark.parametrize("path", [
    "/api/v1/contents",
    "/api/v1/contents/summary",
    "/api/v1/reports",
    "/api/v1/kb/documents",
])
async def test_app_token_grants_read(init_test_db, make_adapter, with_app_token, path):
    app = create_app(adapter=make_adapter(), auth_enabled=True)
    async with app.router.lifespan_context(app):
        await _seed()
        async with _client(app) as client:
            resp = await client.get(path, headers={APP_HEADER: APP_TOKEN})
    assert resp.status_code == 200, f"{path} -> {resp.status_code} {resp.text}"


# ------------------------------------------- 核心：令牌可写（app 的全部功能前提）

async def test_app_token_grants_write(init_test_db, make_adapter, with_app_token, monkeypatch):
    """DELETE 是纯写、不触模型 —— 走 /analyze 会命中真实 LLM（.env 里有 key，
    测试环境同样读得到，那就是一次真实外发调用），所以用删除来证明写权限。"""
    monkeypatch.setattr(settings, "llm_api_key", None)
    app = create_app(adapter=make_adapter(), auth_enabled=True)
    async with app.router.lifespan_context(app):
        await _seed()
        async with _client(app) as client:
            resp = await client.delete("/api/v1/contents/xhs:a", headers={APP_HEADER: APP_TOKEN})
    assert resp.status_code == 204, f"app 令牌写权限丢失: {resp.status_code} {resp.text}"


async def test_app_token_grants_read_only_posts(
    init_test_db, make_adapter, with_app_token, monkeypatch
):
    """POST /kb/search 与 /kb/ask 是 app 的核心读功能（未配 key → 503 而非 401）。"""
    monkeypatch.setattr(settings, "llm_api_key", None)
    app = create_app(adapter=make_adapter(), auth_enabled=True, embedding_adapter=None)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            search = await client.post(
                "/api/v1/kb/search", json={"query": "任意"}, headers={APP_HEADER: APP_TOKEN}
            )
            ask = await client.post(
                "/api/v1/kb/ask", json={"query": "任意"}, headers={APP_HEADER: APP_TOKEN}
            )
    assert search.status_code == 503, f"search -> {search.status_code}"
    assert ask.status_code == 503, f"ask -> {ask.status_code}"


# --------------------------------------------- 核心：采集模块必须被令牌挡在门外

@pytest.mark.parametrize("method,path", [
    ("post", "/api/v1/collection-runs"),
    ("post", "/api/v1/collection-runs/run-1/cancel"),
    ("get", "/api/v1/collection-runs"),
    ("post", "/api/v1/browser/connect"),
    ("post", "/api/v1/browser/retry"),
    ("get", "/api/v1/browser/status"),
])
async def test_app_token_denied_on_collection_endpoints(
    init_test_db, make_adapter, with_app_token, method, path
):
    """采集类端点无论读写都对 app 令牌关门 —— 含动态段的 /cancel 靠前缀匹配覆盖。"""
    app = create_app(adapter=make_adapter(), auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            resp = await getattr(client, method)(path, headers={APP_HEADER: APP_TOKEN})
    assert resp.status_code == 401, f"{method.upper()} {path} -> {resp.status_code}"


async def test_collection_endpoints_still_open_to_admin(
    init_test_db, make_adapter, with_app_token, monkeypatch
):
    """排除只针对 app 令牌 —— 管理员 Cookie 照常能碰采集（web 看板功能不减）。"""
    monkeypatch.setattr(settings, "llm_api_key", None)

    from app.auth import COOKIE_NAME, create_session

    app = create_app(adapter=make_adapter(), auth_enabled=True)
    async with app.router.lifespan_context(app):
        cookies = {COOKIE_NAME: create_session(settings.admin_username)}
        async with _client(app, cookies=cookies) as client:
            resp = await client.get("/api/v1/collection-runs")
    assert resp.status_code == 200, f"管理员读采集列表被误伤: {resp.status_code}"


# ---------------------------------------------------------------- 拒绝

async def test_missing_app_token_401(init_test_db, make_adapter, with_app_token):
    app = create_app(adapter=make_adapter(), auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            resp = await client.get("/api/v1/contents")
    assert resp.status_code == 401


async def test_wrong_app_token_401(init_test_db, make_adapter, with_app_token):
    app = create_app(adapter=make_adapter(), auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            resp = await client.get("/api/v1/contents", headers={APP_HEADER: APP_TOKEN + "x"})
    assert resp.status_code == 401


async def test_app_token_channel_disabled_when_unset(
    init_test_db, make_adapter, without_app_token
):
    """未配置令牌 → 通道关闭。空令牌绝不能放行（否则等于无认证）。"""
    app = create_app(adapter=make_adapter(), auth_enabled=True)
    async with app.router.lifespan_context(app):
        async with _client(app) as client:
            empty = await client.get("/api/v1/contents", headers={APP_HEADER: ""})
            absent = await client.get("/api/v1/contents")
    assert empty.status_code == 401
    assert absent.status_code == 401
