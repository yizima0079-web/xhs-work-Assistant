"""浏览器连接 API 测试：status/connect/retry 三端点（Fake 浏览器适配器，全程离线）。"""
from __future__ import annotations

import httpx
from httpx import ASGITransport, AsyncClient

from app.adapters.base import CommandResult
from app.main import create_app

TABS = [
    {"index": 0, "page": "p1", "url": "https://www.baidu.com/", "title": "百度", "active": True},
    {"index": 1, "page": "p2", "url": "https://www.xiaohongshu.com/explore/x", "title": "小红书", "active": False},
]


class FakeBrowserAdapter:
    """带 browser_* 能力的假适配器：无 xhs 标签时 open 才真正调用。"""

    def __init__(self, tabs: list[dict] | None = None):
        self.tabs = list(tabs or [])
        self.opens: list[str] = []

    async def run(self, command, args):  # 采集契约占位，不在此测
        return CommandResult(command=command, rows=[])

    async def health_check(self):
        from app.domain.models import HealthResult
        return HealthResult(ok=True, logged_in=True, username="t", profile="p")

    def get_capabilities(self):
        from app.domain.models import CapabilityReport
        return CapabilityReport(platform="xhs", commands=["search"], target_types=[])

    async def browser_list_tabs(self, session="datapp") -> CommandResult:
        return CommandResult(command="tab list", rows=self.tabs)

    async def browser_open_page(self, session="datapp", url="https://www.xiaohongshu.com/", window="foreground") -> CommandResult:
        self.opens.append(url)
        new = {"index": 9, "page": "p9", "url": url, "title": "小红书", "active": True}
        self.tabs.append(new)
        return CommandResult(command="open", rows=[new])


async def _client(adapter):
    app = create_app(adapter=adapter)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            yield client


async def test_status_lists_tabs_and_detects_xhs(init_test_db):
    a = FakeBrowserAdapter(TABS)
    async for client in _client(a):
        r = await client.get("/api/v1/browser/status")
        assert r.status_code == 200
        body = r.json()
        assert body["reachable"] is True
        assert body["xhs_open"] is True
        assert len(body["tabs"]) == 2


async def test_status_no_xhs(init_test_db):
    a = FakeBrowserAdapter([{"index": 0, "url": "https://www.baidu.com/", "title": "百度"}])
    async for client in _client(a):
        r = await client.get("/api/v1/browser/status")
        assert r.status_code == 200
        assert r.json()["xhs_open"] is False


async def test_connect_opens_xhs(init_test_db):
    a = FakeBrowserAdapter([])
    async for client in _client(a):
        r = await client.post("/api/v1/browser/connect")
        assert r.status_code == 200
        body = r.json()
        assert body["action"] == "connected"
        assert body["xhs_open"] is True
        assert a.opens == ["https://www.xiaohongshu.com/"]


async def test_retry_reuses_existing_xhs_tab(init_test_db):
    a = FakeBrowserAdapter(TABS)
    async for client in _client(a):
        r = await client.post("/api/v1/browser/retry")
        assert r.status_code == 200
        assert r.json()["action"] == "reused"
        assert r.json()["xhs_open"] is True
        assert a.opens == []  # 未重开


async def test_retry_opens_when_missing(init_test_db):
    a = FakeBrowserAdapter([{"index": 0, "url": "https://www.baidu.com/", "title": "百度"}])
    async for client in _client(a):
        r = await client.post("/api/v1/browser/retry")
        assert r.status_code == 200
        assert r.json()["action"] == "opened"
        assert a.opens == ["https://www.xiaohongshu.com/"]


async def test_unsupported_adapter_returns_503(init_test_db, make_adapter):
    async for client in _client(make_adapter()):
        r = await client.get("/api/v1/browser/status")
        assert r.status_code == 503
        r2 = await client.post("/api/v1/browser/connect")
        assert r2.status_code == 503
