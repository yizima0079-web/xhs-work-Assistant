"""向量嵌入适配层：DashScope 原生多模态端点（tongyi-embedding-vision-flash）。

注意该模型不支持 OpenAI compatible-mode /embeddings，必须走
POST {base}/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding。
每个 input 项可带 text 与/或 image（图片可选；KB 层封面失败退纯文本）。transport 可注入
以便离线测试；key 脱敏仿 llm.py。无 key → 不建客户端、is_configured()==False，服务层 503。
"""
from __future__ import annotations

from typing import Any, Protocol

import httpx

_MAX_BODY_SNIPPET = 200


class EmbeddingError(Exception):
    """向量层错误基类。"""


class EmbeddingNotConfiguredError(EmbeddingError):
    """服务端未配置 key → 503。绝不伪造向量。"""


class EmbeddingProviderError(EmbeddingError):
    """网络/超时/HTTP 错误 → 502（服务不可达，可重试）。"""


class EmbeddingParseError(EmbeddingError):
    """模型响应无法解析出向量 → 503（产出不可用，不落库）。"""


def _provider_message(status_code: int, body: str) -> str:
    snippet = (body or "").strip()[:_MAX_BODY_SNIPPET]
    return f"向量服务返回 HTTP {status_code}: {snippet}" if snippet else f"向量服务返回 HTTP {status_code}"


class EmbeddingAdapter(Protocol):
    def is_configured(self) -> bool: ...

    async def embed(self, items: list[dict[str, Any]]) -> list[list[float]]: ...


class DashScopeEmbeddingAdapter:
    """原生 DashScope 多模态 embedding。item: {"text": str?, "image": url?}，至少其一。"""

    _PATH = "/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"

    def __init__(
        self,
        base_url: str,
        api_key: str | None,
        model: str,
        dimension: int = 768,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._dimension = dimension
        self._timeout = timeout
        self._transport = transport

    def is_configured(self) -> bool:
        return bool(self._api_key)

    async def embed(self, items: list[dict[str, Any]]) -> list[list[float]]:
        if not self.is_configured():
            raise EmbeddingNotConfiguredError("服务端未配置向量 API key（DATAPP_LLM_API_KEY）")
        if not items:
            return []
        inputs: list[dict[str, Any]] = []
        for item in items:
            entry: dict[str, Any] = {}
            if isinstance(item.get("text"), str) and item["text"].strip():
                entry["text"] = item["text"].strip()
            if isinstance(item.get("image"), str) and item["image"].strip():
                entry["image"] = item["image"].strip()
            if not entry:
                raise EmbeddingParseError("input 项须含 text 或 image")
            inputs.append(entry)

        payload = {
            "model": self._model,
            "input": {"contents": inputs},
            "parameters": {"dimension": self._dimension},
        }
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
                    f"{self._base_url}{self._PATH}", headers=headers, json=payload
                )
        except httpx.HTTPError as exc:
            raise EmbeddingProviderError(f"向量服务不可达: {type(exc).__name__}") from exc

        if resp.status_code != 200:
            message = _provider_message(resp.status_code, resp.text)
            if self._api_key:
                message = message.replace(self._api_key, "***")
            raise EmbeddingProviderError(message)
        try:
            embeddings = resp.json()["output"]["embeddings"]
        except (KeyError, ValueError) as exc:
            raise EmbeddingParseError(f"向量响应结构不完整: {type(exc).__name__}") from exc
        if not isinstance(embeddings, list) or len(embeddings) != len(inputs):
            raise EmbeddingParseError(f"向量条数不匹配（期望 {len(inputs)}，得 {len(embeddings)}）")

        # 服务端可能乱序 → 按 index 重排；无 index 字段则 stable sort 保原序
        ordered = sorted(
            embeddings,
            key=lambda e: e.get("index", 0) if isinstance(e, dict) else 0,
        )
        out: list[list[float]] = []
        for e in ordered:
            vec = e.get("embedding") if isinstance(e, dict) else None
            if not isinstance(vec, list) or not vec:
                raise EmbeddingParseError("embedding 字段缺失或为空")
            out.append([float(x) for x in vec])
        return out
