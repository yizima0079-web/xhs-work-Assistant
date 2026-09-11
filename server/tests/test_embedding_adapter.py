"""DashScopeEmbeddingAdapter 离线单测：MockTransport 校验 body/路径/重排/脱敏/错误映射。"""
from __future__ import annotations

import json

import httpx
import pytest

from app.adapters.embedding import (
    DashScopeEmbeddingAdapter,
    EmbeddingNotConfiguredError,
    EmbeddingParseError,
    EmbeddingProviderError,
)

KEY = "sk-test-secret-abc"
MODEL = "tongyi-embedding-vision-flash"
_PATH = "/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding"


def _adapter(handler, key=KEY, base_url="https://dashscope.aliyuncs.com") -> DashScopeEmbeddingAdapter:
    transport = httpx.MockTransport(handler)
    return DashScopeEmbeddingAdapter(base_url, key, MODEL, dimension=768, timeout=5.0, transport=transport)


def _ok_response(embeddings: list[dict]) -> httpx.Response:
    return httpx.Response(200, json={"output": {"embeddings": embeddings}})


async def test_post_path_and_body_shape():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return _ok_response([{"index": 0, "embedding": [0.1, 0.2]}, {"index": 1, "embedding": [0.3, 0.4]}])

    adapter = _adapter(handler)
    out = await adapter.embed([{"text": "你好"}] * 2)
    assert seen["url"] == f"https://dashscope.aliyuncs.com{_PATH}"
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["body"]["model"] == MODEL
    assert seen["body"]["parameters"]["dimension"] == 768
    assert seen["body"]["input"] == {"contents": [{"text": "你好"}, {"text": "你好"}]}
    assert out == [[0.1, 0.2], [0.3, 0.4]]


async def test_reorder_by_index():
    async def handler(request: httpx.Request) -> httpx.Response:
        return _ok_response([
            {"index": 1, "embedding": [1.0, 2.0]},
            {"index": 0, "embedding": [3.0, 4.0]},
        ])

    out = await _adapter(handler).embed([{"text": "a"}, {"text": "b"}])
    assert out == [[3.0, 4.0], [1.0, 2.0]]


async def test_float_conversion():
    async def handler(request: httpx.Request) -> httpx.Response:
        return _ok_response([{"index": 0, "embedding": [1, "2.5", 3]}])

    out = await _adapter(handler).embed([{"text": "x"}])
    assert out == [[1.0, 2.5, 3.0]]


async def test_count_mismatch_raises_parse():
    async def handler(request: httpx.Request) -> httpx.Response:
        return _ok_response([{"index": 0, "embedding": [1.0]}])  # 2 input → 1 output

    with pytest.raises(EmbeddingParseError):
        await _adapter(handler).embed([{"text": "a"}, {"text": "b"}])


async def test_missing_output_structure_raises_parse():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output": {}})

    with pytest.raises(EmbeddingParseError):
        await _adapter(handler).embed([{"text": "a"}])


async def test_empty_embedding_field_raises_parse():
    async def handler(request: httpx.Request) -> httpx.Response:
        return _ok_response([{"index": 0, "embedding": []}])

    with pytest.raises(EmbeddingParseError):
        await _adapter(handler).embed([{"text": "a"}])


async def test_provider_error_redacts_key():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text=f"bad key: {KEY} quota exceeded")

    with pytest.raises(EmbeddingProviderError) as ei:
        await _adapter(handler).embed([{"text": "a"}])
    assert KEY not in str(ei.value)
    assert "***" in str(ei.value)


async def test_http_error_maps_to_provider():
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("conn refused")

    with pytest.raises(EmbeddingProviderError):
        await _adapter(handler).embed([{"text": "a"}])


async def test_no_key_not_configured():
    adapter = _adapter(lambda r: _ok_response([{"index": 0, "embedding": [1.0]}]), key=None)
    assert adapter.is_configured() is False
    with pytest.raises(EmbeddingNotConfiguredError):
        await adapter.embed([{"text": "a"}])


async def test_empty_items_returns_empty():
    adapter = _adapter(lambda r: _ok_response([]))
    assert await adapter.embed([]) == []


async def test_item_requires_text_or_image():
    async def handler(request: httpx.Request) -> httpx.Response:
        return _ok_response([{"index": 0, "embedding": [1.0]}])

    with pytest.raises(EmbeddingParseError):
        await _adapter(handler).embed([{"some": "thing"}])
