"""API 集成测试：创建任务 -> 后台采集 -> 落库 -> 去重。"""
from __future__ import annotations

import asyncio

import httpx
from httpx import ASGITransport, AsyncClient

from app.adapters.base import CommandResult
from app.main import create_app

_TERMINAL = {"success", "partial", "failed", "blocked", "cancelled"}


async def _wait_done(client: httpx.AsyncClient, run_id: str) -> dict:
    for _ in range(100):
        rr = await client.get(f"/api/v1/collection-runs/{run_id}")
        d = rr.json()
        if d["run"]["status"] in _TERMINAL:
            return d
        await asyncio.sleep(0.02)
    raise AssertionError("run did not finish")


async def test_health(init_test_db, make_adapter):
    app = create_app(adapter=make_adapter())
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.get("/api/v1/health")
            assert r.status_code == 200
            assert r.json()["logged_in"] is True


async def test_health_degrades_when_opencli_process_cannot_start(init_test_db):
    from app.adapters.opencli import OpenCLIAdapter
    from app.config import settings

    class BrokenAdapter(OpenCLIAdapter):
        async def run(self, command, args):
            raise PermissionError("pipe denied")

    app = create_app(adapter=BrokenAdapter(settings))
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.get("/api/v1/health")
            assert r.status_code == 200
            assert r.json()["ok"] is False
            assert "OpenCLI" in r.json()["detail"]


async def test_collect_keyword_flow_and_dedup(init_test_db, make_adapter, load_fixture):
    search_rows = load_fixture("search.json")
    adapter = make_adapter({
        "search": CommandResult(
            command="search", rows=search_rows, raw='[{"title":"AI眼镜实测分享"}]'
        ),
    })
    app = create_app(adapter=adapter)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            # 第一次采集
            r = await client.post(
                "/api/v1/collection-runs",
                json={"target_type": "keyword", "target": "AI眼镜", "max_items": 2},
            )
            assert r.status_code == 201
            d = await _wait_done(client, r.json()["id"])
            assert d["run"]["status"] == "success"
            assert d["run"]["items_found"] == 3
            assert d["run"]["items_saved"] == 3
            assert d["events"]
            # 工具版本随任务落库（手册 §4.3）：FakeAdapter 无该属性时退回落默认版本
            assert d["run"]["tool_version"] == "opencli@1.8.8"

            # 内容已入库
            cl = await client.get("/api/v1/contents")
            assert len(cl.json()) == 3

            # 重复任务：去重，0 重复写入
            r2 = await client.post(
                "/api/v1/collection-runs",
                json={"target_type": "keyword", "target": "AI眼镜", "max_items": 2},
            )
            d2 = await _wait_done(client, r2.json()["id"])
            assert d2["run"]["status"] == "success"
            assert d2["run"]["items_saved"] == 0  # 去重
            cl2 = await client.get("/api/v1/contents")
            assert len(cl2.json()) == 3


async def test_blocked_on_breaker(init_test_db, make_adapter, monkeypatch):
    # 硬失败（SECURITY_BLOCK）连续命中 -> 熔断 -> 后续任务 blocked
    from app.adapters.base import CommandError
    from app.config import settings
    from app.domain.enums import ErrorCategory

    adapter = make_adapter({
        "search": CommandResult(
            command="search",
            error=CommandError(
                "SECURITY_BLOCK", "blocked", category=ErrorCategory.RATE_LIMIT, retryable=False
            ),
        ),
    })
    # 阈值调低到 1，便于触发（monkeypatch 自动还原，避免污染其它测试）
    monkeypatch.setattr(settings, "circuit_breaker_threshold", 1)
    app = create_app(adapter=adapter)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(
                "/api/v1/collection-runs",
                json={"target_type": "keyword", "target": "AI眼镜", "max_items": 2},
            )
            d = await _wait_done(client, r.json()["id"])
            assert d["run"]["status"] == "blocked"
            assert d["run"]["error_category"] == "rate_limit"

            # 熔断后：新任务直接 blocked，不再调用适配器
            calls_before = len(adapter.calls)
            r2 = await client.post(
                "/api/v1/collection-runs",
                json={"target_type": "keyword", "target": "AI眼镜", "max_items": 2},
            )
            d2 = await _wait_done(client, r2.json()["id"])
            assert d2["run"]["status"] == "blocked"
            assert d2["run"]["error_code"] == "CIRCUIT_OPEN"
            assert len(adapter.calls) == calls_before  # 未调用适配器


async def test_cancel_terminates_inflight_command(init_test_db):
    """取消正在跑的采集：终止在途命令并落 cancelled，而不是等它自己超时。"""
    from app.adapters.base import CommandError
    from app.domain.enums import ErrorCategory
    from app.domain.models import CapabilityReport, HealthResult

    class HangingAdapter:
        """命令一直不返回，直到 terminate_active 放行它（模拟 kill 子进程后命令立刻返回）。"""

        tool_version = "opencli@test"

        def __init__(self):
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.terminated = 0
            self.calls = 0

        async def run(self, command, args):
            self.calls += 1
            self.entered.set()
            await self.release.wait()
            return CommandResult(
                command=command,
                error=CommandError(
                    "TIMEOUT", "killed", category=ErrorCategory.TIMEOUT, retryable=False
                ),
            )

        async def terminate_active(self) -> int:
            self.terminated += 1
            self.release.set()
            return 1

        async def health_check(self):
            return HealthResult(ok=True, logged_in=True)

        def get_capabilities(self):
            return CapabilityReport(platform="xhs", commands=["search"], target_types=[])

    adapter = HangingAdapter()
    app = create_app(adapter=adapter)
    transport = ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(
                "/api/v1/collection-runs",
                json={"target_type": "keyword", "target": "AI眼镜", "max_items": 1},
            )
            assert r.status_code == 201
            run_id = r.json()["id"]

            await asyncio.wait_for(adapter.entered.wait(), timeout=5)
            c = await client.post(f"/api/v1/collection-runs/{run_id}/cancel")
            assert c.status_code == 200

            d = await _wait_done(client, run_id)
            assert d["run"]["status"] == "cancelled"
            assert adapter.terminated == 1
            assert d["run"]["tool_version"] == "opencli@test"
