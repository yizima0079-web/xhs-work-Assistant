"""健康检查：适配器登录态 + 熔断状态（带 TTL 缓存）。

`adapter.health_check()` 每次都会 spawn 一个 OpenCLI 子进程，看板轮询下这个开销
不可接受，因此加 TTL 缓存。缓存必须诚实：命中时如实给出 cached=true 与真实探测
时刻 checked_at，不把 30 秒前的陈旧结论伪装成刚刚测出来的。
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app.auth import app_token_ok, session_user
from app.config import settings
from app.domain.models import HealthResult
from app.schemas.api import HealthResponse
from app.services.lc import LC_BACKEND, LC_TRACING
from app.services.redact import redact_username

router = APIRouter()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@router.get("/api/v1/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    adapter = request.app.state.adapter
    throttle = request.app.state.throttle
    cache = request.app.state.health_cache

    now = time.monotonic()
    cached = cache["probe"] is not None and (now - cache["at"]) < settings.health_cache_ttl
    if not cached:
        try:
            cache["probe"] = await adapter.health_check()
        except OSError as exc:
            # 子进程/管道创建失败时健康接口必须降级返回，不能让探针变成 500。
            # 失败结论同样进缓存：否则 OpenCLI 不可用期间每次轮询都在白 spawn 进程。
            cache["probe"] = f"OpenCLI 进程不可用: {type(exc).__name__}"
        cache["at"] = now
        cache["checked_at"] = _now_iso()

    probe = cache["probe"]
    common = {
        "breaker_open": throttle.breaker.open_platforms(),
        "lc": LC_BACKEND,
        "lc_tracing": LC_TRACING,
        "cached": cached,
        "checked_at": cache["checked_at"],
    }
    if isinstance(probe, HealthResult):
        # 仅当开启认证 (auth_enabled=True) 且属于非管理员凭证请求时脱敏账号名，防公开探针泄漏真实用户名
        auth_enabled = getattr(request.app.state, "auth_enabled", False)
        is_authenticated = (not auth_enabled) or bool(session_user(request) or app_token_ok(request))
        display_username = probe.username if is_authenticated else redact_username(probe.username)

        return HealthResponse(
            ok=probe.ok,
            logged_in=probe.logged_in,
            username=display_username,
            profile=probe.profile,
            detail=probe.detail,
            **common,
        )
    return HealthResponse(ok=False, logged_in=False, detail=probe, **common)
