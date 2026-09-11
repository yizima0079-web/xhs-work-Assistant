"""健康检查 TTL 缓存：少 spawn 子进程，但必须如实标注缓存与真实探测时刻。

诚实性是这个改动的前提 —— 缓存了却报 cached=false / 时间戳乱跳，就是拿陈旧结论
冒充实时，属于本项目明令禁止的「伪造成功」。
"""
from __future__ import annotations

import httpx
import pytest

from app.domain.models import HealthResult
from app.main import create_app


class CountingAdapter:
    """记录 health_check 调用次数的适配器；可选让探测抛 OSError。"""

    def __init__(self, raise_oserror: bool = False):
        self.calls = 0
        self.raise_oserror = raise_oserror

    async def health_check(self) -> HealthResult:
        self.calls += 1
        if self.raise_oserror:
            raise OSError("spawn failed")
        return HealthResult(ok=True, logged_in=True, username="tester", profile="p")

    async def run(self, command, args):  # pragma: no cover - 本测试不用
        raise AssertionError("health 不应触达 run")


async def _get(client, path="/api/v1/health"):
    r = await client.get(path)
    assert r.status_code == 200, r.text
    return r.json()


async def test_second_call_within_ttl_is_cached(init_test_db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "health_cache_ttl", 30.0)
    adapter = CountingAdapter()
    app = create_app(adapter=adapter)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            first = await _get(client)
            second = await _get(client)

    assert adapter.calls == 1                 # 第二次没有再次 spawn 子进程
    assert first["cached"] is False           # 首次是真实探测
    assert first["checked_at"]
    assert second["cached"] is True
    assert second["checked_at"] == first["checked_at"]   # 反映真实探测时刻，不刷新
    assert second["logged_in"] is True and second["username"] == "tester"


async def test_ttl_zero_always_probes(init_test_db, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "health_cache_ttl", 0.0)
    adapter = CountingAdapter()
    app = create_app(adapter=adapter)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            a = await _get(client)
            b = await _get(client)

    assert adapter.calls == 2
    assert a["cached"] is False and b["cached"] is False


async def test_oserror_degrades_and_is_cached(init_test_db, monkeypatch):
    """子进程起不来时降级返回（不是 500），且失败结论同样进缓存，不白 spawn。"""
    from app.config import settings

    monkeypatch.setattr(settings, "health_cache_ttl", 30.0)
    adapter = CountingAdapter(raise_oserror=True)
    app = create_app(adapter=adapter)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            first = await _get(client)
            second = await _get(client)

    assert adapter.calls == 1
    assert first["ok"] is False and first["logged_in"] is False
    assert "OpenCLI 进程不可用" in first["detail"]
    assert second["cached"] is True
    assert "OpenCLI 进程不可用" in second["detail"]
