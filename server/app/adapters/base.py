"""采集适配器契约（手册 §4.1）与底层命令结果/错误分类。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Awaitable, Protocol

from app.domain.enums import ErrorCategory
from app.domain.models import (
    CapabilityReport,
    CollectRequest,
    CollectResult,
    HealthResult,
)

# 风控/限速信号：命中即熔断且禁止重试（AGENTS.md：429 / 403 / 验证码 / 300017 / 300031 立即熔断）。
# 平台把风控藏在文案里（"安全限制"、"访问链接异常"）而不是错误码里，所以码和文案一起扫。
_RISK_TEXT_PATTERNS = (
    "captcha",
    "验证码",
    "滑块",
    "geetest",
    "安全限制",
    "访问链接异常",
    "请求太频繁",
    "操作太频繁",
    "操作过于频繁",
    "风控",
    "rate limit",
    "too many requests",
    "forbidden",
)
# 数字码加词边界：note_id 之类的十六进制串里出现 403 不算风控
_RISK_CODE_RE = re.compile(r"\b(?:300017|300031|429|403)\b")

# 采集环境不可用：daemon / 扩展 / Bridge 不在线，重试没有意义，先人工把浏览器拉起来
_ENV_UNAVAILABLE_PATTERNS = (
    "extension is not connected",
    "extension not connected",
    "no extension connected",
    "daemon is not running",
    "daemon not running",
    "econnrefused",
    "browser bridge",
    "profile is required",
    "profile disconnected",
    "not reachable",
)


def is_risk_signal(code: str | None, message: str | None) -> bool:
    """错误码/文案是否构成风控或限速信号（用于熔断与 rate_limit_signal 标记）。"""
    text = f"{code or ''} {message or ''}".lower()
    return bool(_RISK_CODE_RE.search(text)) or any(p in text for p in _RISK_TEXT_PATTERNS)


@dataclass
class CommandError:
    code: str
    message: str
    exit_code: int | None = None
    category: ErrorCategory = ErrorCategory.PARSE
    retryable: bool = False


@dataclass
class CommandResult:
    command: str
    rows: list[dict] = field(default_factory=list)
    raw: str | None = None
    error: CommandError | None = None
    exit_code: int = 0
    duration_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.error is None


def classify_error(code: str | None, message: str) -> tuple[ErrorCategory, bool]:
    """把 OpenCLI 错误码/文案映射为 (分类, 是否可重试)。

    分类与可重试是两件事：`NETWORK` 也可能是不可重试的（扩展没连上，重试多少次都一样）。
    判定顺序有意如此 —— 先认「导航后连接断」这类可恢复瞬态，再认风控（熔断），
    最后才落到适配器漂移（PARSE，不可重试也不熔断）。
    """
    code = code or ""
    msg = message or ""
    text = f"{code} {msg}".lower()

    # Browser connection dropped = command_result_unknown（瞬态，重试 1 次，不计风控）
    if (
        code == "command_result_unknown"
        or "browser connection dropped" in text
        or "may have completed" in text
    ):
        return ErrorCategory.NETWORK, True
    if code == "TIMEOUT":
        return ErrorCategory.TIMEOUT, False
    if code in ("AUTH_REQUIRED", "LOGIN_WALL"):
        return ErrorCategory.AUTH, False
    # 风控/限速：熔断 + 禁止重试，等人工确认
    if code == "SECURITY_BLOCK" or is_risk_signal(code, msg):
        return ErrorCategory.RATE_LIMIT, False
    # 采集环境不可用：不可重试（重试也只是再失败一次），提示先恢复浏览器/扩展
    if any(p in text for p in _ENV_UNAVAILABLE_PATTERNS):
        return ErrorCategory.NETWORK, False
    if code == "ARGUMENT":
        return ErrorCategory.ARGUMENT, False
    if code == "EMPTY_RESULT":
        return ErrorCategory.EMPTY, False
    # 其余一律按「适配器/页面结构漂移」处理：不熔断，也不重试
    return ErrorCategory.PARSE, False


def is_breaker(category: ErrorCategory) -> bool:
    """硬失败（风控/登录墙）是否触发熔断计数。"""
    return category in (ErrorCategory.AUTH, ErrorCategory.RATE_LIMIT)


class CollectorAdapter(Protocol):
    """统一采集适配器契约。业务层只依赖本接口，不依赖 OpenCLI 内部。"""

    def health_check(self) -> Awaitable[HealthResult]: ...

    def get_capabilities(self) -> CapabilityReport: ...

    async def run(self, command: str, args: list[str]) -> CommandResult: ...

    async def collect(self, request: CollectRequest) -> CollectResult: ...
