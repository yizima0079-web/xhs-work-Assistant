"""模型适配层：OpenAI 兼容 /chat/completions（默认 DashScope 兼容端点）。

惰性装配：无 key 时 `is_configured() == False`，不建 httpx 客户端、不发请求；
服务层触达审核/报告才报 ModelNotConfiguredError(503)。无 key / 模型失败时
绝不用假数据兜底——模型输出不是事实，宁可 502/503 也不伪造成功。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_MAX_BODY_SNIPPET = 200  # 错误信息最多带响应体前 N 字符，防止泄露敏感内容


@dataclass
class ChatMessage:
    role: str  # system | user
    content: str


class ModelError(Exception):
    """模型层错误基类。"""


class ModelNotConfiguredError(ModelError):
    """服务端未配置模型 key → 503。绝不伪造输出。"""


class ModelProviderError(ModelError):
    """网络/超时/HTTP 错误 → 502（服务不可达，可重试）。"""


class ModelParseError(ModelError):
    """模型响应无法解析出合法 JSON → 503（产出不可审计，不落库）。"""


class ModelAdapter(Protocol):
    def is_configured(self) -> bool: ...

    async def chat_json(self, messages: list[ChatMessage]) -> dict: ...

    async def chat_text(self, messages: list[ChatMessage]) -> str: ...


def _extract_json(text: str) -> dict:
    """从模型返回文本剥出 JSON 对象：剥围栏 → 截首尾花括号 → json.loads。"""
    if not text or not isinstance(text, str):
        raise ModelParseError("模型返回空内容")
    fenced = _JSON_FENCE_RE.search(text)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise ModelParseError("响应中未找到 JSON 对象")
    try:
        data = json.loads(candidate[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ModelParseError(f"JSON 解析失败: {exc}") from exc
    if not isinstance(data, dict):
        raise ModelParseError(f"顶层不是对象: {type(data).__name__}")
    return data


def _provider_message(status_code: int, body: str) -> str:
    snippet = (body or "").strip()[:_MAX_BODY_SNIPPET]
    return f"模型服务返回 HTTP {status_code}: {snippet}" if snippet else f"模型服务返回 HTTP {status_code}"


class DashScopeModelAdapter:
    """OpenAI 兼容 chat/completions 客户端。transport 可注入以便离线测试。"""

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._transport = transport

    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def _chat(self, messages: list[ChatMessage], json_mode: bool) -> str:
        """共用的 chat/completions 调用。json_mode 控制是否强制 response_format。"""
        if not self.is_configured():
            raise ModelNotConfiguredError("服务端未配置模型 API key（DATAPP_LLM_API_KEY）")
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        client_kwargs: dict[str, Any] = {"timeout": self._timeout}
        if self._transport is not None:
            client_kwargs["transport"] = self._transport
        try:
            async with httpx.AsyncClient(**client_kwargs) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions", headers=headers, json=payload
                )
        except httpx.HTTPError as exc:
            raise ModelProviderError(f"模型服务不可达: {type(exc).__name__}") from exc

        if resp.status_code != 200:
            message = _provider_message(resp.status_code, resp.text)
            if self._api_key:
                message = message.replace(self._api_key, "***")
            raise ModelProviderError(message)
        try:
            data = resp.json()
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise ModelParseError(f"模型响应结构不完整: {type(exc).__name__}") from exc

    async def chat_json(self, messages: list[ChatMessage]) -> dict:
        return _extract_json(await self._chat(messages, json_mode=True))

    async def chat_text(self, messages: list[ChatMessage]) -> str:
        """返回模型原文（markdown 清洗等自由文本任务）。空响应仍判 Parse 错误。"""
        content = await self._chat(messages, json_mode=False)
        if not content or not content.strip():
            raise ModelParseError("模型返回空内容")
        return content
