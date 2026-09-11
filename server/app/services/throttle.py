"""采集限速与熔断（手册 §4.4）：每平台令牌桶 + 连续硬失败熔断。"""
from __future__ import annotations

import asyncio
import random
import time

from app.adapters.base import is_breaker
from app.domain.enums import ErrorCategory


class TokenBucket:
    """令牌桶限速。全局并发=1 时退化为单流节流，仍保留锁以兼容未来扩并发。

    `jitter` 把不足一个令牌时的等待随机放大到 [1, 1+jitter] 倍（手册 §4.4 的
    「有上限的随机抖动」）。**只增不减**：抖动不会把间隔压到比配置值更短，
    既守住下限，也不被当成规避检测的节奏扰动。
    """

    def __init__(self, rate: float, capacity: int, jitter: float = 0.0):
        self.rate = rate
        self.capacity = capacity
        self.jitter = max(0.0, jitter)
        self.tokens = float(capacity)
        self._lock = asyncio.Lock()
        self._last = time.monotonic()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self._last) * self.rate)
                self._last = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                need = (1.0 - self.tokens) / self.rate
                if self.jitter:
                    need *= 1.0 + random.random() * self.jitter
                await asyncio.sleep(need)


class CircuitBreaker:
    """连续硬失败（风控/登录墙）达阈值 → open（blocked），人工 reset 恢复。"""

    def __init__(self, threshold: int):
        self.threshold = threshold
        self._failures: dict[str, int] = {}
        self._open: set[str] = set()
        self._lock = asyncio.Lock()

    def is_open(self, platform: str) -> bool:
        return platform in self._open

    def open_platforms(self) -> list[str]:
        return sorted(self._open)

    async def record_failure(self, platform: str) -> bool:
        """记录一次硬失败；返回是否由此触发熔断。"""
        async with self._lock:
            n = self._failures.get(platform, 0) + 1
            self._failures[platform] = n
            if n >= self.threshold:
                self._open.add(platform)
                return True
            return False

    async def record_success(self, platform: str) -> None:
        async with self._lock:
            self._failures[platform] = 0

    async def reset(self, platform: str) -> None:
        async with self._lock:
            self._failures[platform] = 0
            self._open.discard(platform)


class Throttle:
    """组合：每平台令牌桶 + 熔断器。"""

    def __init__(self, rate: float, capacity: int, breaker_threshold: int, jitter: float = 0.0):
        self.rate = rate
        self.capacity = capacity
        self.jitter = max(0.0, jitter)
        self._buckets: dict[str, TokenBucket] = {}
        self.breaker = CircuitBreaker(breaker_threshold)

    def _bucket(self, platform: str) -> TokenBucket:
        b = self._buckets.get(platform)
        if b is None:
            b = TokenBucket(self.rate, self.capacity, self.jitter)
            self._buckets[platform] = b
        return b

    async def acquire(self, platform: str) -> None:
        await self._bucket(platform).acquire()

    async def record_result(self, platform: str, category: ErrorCategory | None) -> None:
        """按结果更新熔断计数。硬失败计数，成功/其它清零。"""
        if category is not None and is_breaker(category):
            await self.breaker.record_failure(platform)
        else:
            await self.breaker.record_success(platform)
