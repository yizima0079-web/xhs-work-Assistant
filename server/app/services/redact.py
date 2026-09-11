"""日志/事件脱敏：URL 查询参数里的令牌类值不外流。

AGENTS.md：含 `xsec_token` 的 URL 必须脱敏保存，日志与报告要脱敏完整来源查询参数。
这里只负责「展示与日志」这一层 —— 采集输入（run.target）必须原样保留才能重放任务，
敏感值以受控方式留在库里，但不进入事件流、日志与报告文本。
"""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 命中即打码的参数名（不区分大小写，子串匹配）：token/sign 系签名参数都在其中
_SENSITIVE_HINTS = ("token", "sign", "signature", "secret", "password", "auth")
_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")
_MASK = "***"


def redact_url(url: str) -> str:
    """把 URL 中敏感查询参数的值替换成 ***；无查询串或解析失败时原样返回。"""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    if not pairs:
        return url
    redacted = [
        (k, _MASK if any(h in k.lower() for h in _SENSITIVE_HINTS) else v) for k, v in pairs
    ]
    if redacted == pairs:
        return url
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(redacted, safe="*"), parts.fragment)
    )


def redact_text(text: str | None) -> str | None:
    """把文本中出现的所有 URL 逐个脱敏；None/空串原样返回。"""
    if not text:
        return text
    return _URL_RE.sub(lambda m: redact_url(m.group(0)), text)


def redact_username(username: str | None) -> str | None:
    """脱敏用户名字符串（例 Melody -> M***y），防止公开探针泄漏账号名。"""
    if not username:
        return username
    if len(username) <= 2:
        return username[0] + "*"
    return username[0] + "*" * (len(username) - 2) + username[-1]
