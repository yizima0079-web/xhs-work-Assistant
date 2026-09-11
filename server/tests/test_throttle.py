"""限速与熔断单元测试。"""
from __future__ import annotations

import time

from app.domain.enums import ErrorCategory
from app.services.throttle import CircuitBreaker, Throttle, TokenBucket


async def test_token_bucket_refills_after_exhaust():
    b = TokenBucket(rate=100.0, capacity=1)
    await b.acquire()  # 初始满，立即
    t0 = time.monotonic()
    await b.acquire()  # 耗尽后需等 ~10ms
    assert time.monotonic() - t0 >= 0.008


async def test_token_bucket_jitter_only_delays_more():
    """抖动是乘法放大（[1, 1+jitter]），不得把间隔压到比配置值更短。"""
    b = TokenBucket(rate=1000.0, capacity=1, jitter=0.5)  # 基数 1ms
    await b.acquire()
    t0 = time.monotonic()
    await b.acquire()
    dt = time.monotonic() - t0
    assert dt >= 0.0008  # 下限仍是配置的 1ms（留调度余量）
    assert dt <= 0.05  # 上限 1.5ms，给慢机器留足余量


async def test_circuit_breaker_opens_at_threshold():
    cb = CircuitBreaker(threshold=2)
    assert not cb.is_open("xhs")
    assert await cb.record_failure("xhs") is False
    assert await cb.record_failure("xhs") is True  # 达到阈值触发
    assert cb.is_open("xhs")
    assert cb.open_platforms() == ["xhs"]
    await cb.reset("xhs")
    assert not cb.is_open("xhs")


async def test_circuit_breaker_success_resets_count():
    cb = CircuitBreaker(threshold=3)
    await cb.record_failure("xhs")
    await cb.record_success("xhs")
    await cb.record_failure("xhs")
    await cb.record_failure("xhs")
    assert not cb.is_open("xhs")  # 成功清零后 2 < 3


async def test_throttle_record_result_breaker_only_on_hard_failure():
    t = Throttle(rate=1.0, capacity=1, breaker_threshold=2)
    await t.record_result("xhs", ErrorCategory.NETWORK)  # 非硬失败 → 清零
    await t.record_result("xhs", ErrorCategory.RATE_LIMIT)
    await t.record_result("xhs", ErrorCategory.RATE_LIMIT)
    assert t.breaker.is_open("xhs")
