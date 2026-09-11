"""模型适配层测试：_extract_json + DashScope 请求构造（httpx MockTransport 离线）。"""
from __future__ import annotations

import json

import httpx
import pytest

from app.adapters.llm import (
    ChatMessage,
    DashScopeModelAdapter,
    ModelNotConfiguredError,
    ModelParseError,
    ModelProviderError,
    _extract_json,
)
from app.services.prompt_templates import build_extract_messages, build_judge_messages

KEY = "sk-test-not-a-real-key"


def test_extract_json_bare_object():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced_and_surrounded():
    text = "好的，结果如下：\n```json\n{\"claims\": []}\n```\n希望对你有帮助。"
    assert _extract_json(text) == {"claims": []}


def test_extract_json_invalid_raises():
    for bad in ["", "没有 JSON", '{"broken":', "[1,2,3]", None, 123]:
        with pytest.raises(ModelParseError):
            _extract_json(bad)


def _json_response(payload: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"message": {"content": json.dumps(payload, ensure_ascii=False)}}]},
    )


def _adapter(handler) -> DashScopeModelAdapter:
    transport = httpx.MockTransport(handler)
    return DashScopeModelAdapter(
        "https://dashscope.example/v1", KEY, "qwen3.8-flash", timeout=1.0, transport=transport
    )


async def test_chat_json_builds_request_and_parses():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return _json_response({"status": "supported"})

    adapter = _adapter(handler)
    result = await adapter.chat_json([ChatMessage(role="user", content="hi")])

    assert result == {"status": "supported"}
    assert captured["url"] == "https://dashscope.example/v1/chat/completions"
    assert captured["headers"]["authorization"] == f"Bearer {KEY}"
    body = captured["body"]
    assert body["model"] == "qwen3.8-flash"
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"] == [{"role": "user", "content": "hi"}]


async def test_chat_json_http_error_does_not_leak_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text=f"invalid api key {KEY}")

    adapter = _adapter(handler)
    with pytest.raises(ModelProviderError) as exc:
        await adapter.chat_json([ChatMessage(role="user", content="hi")])
    assert KEY not in str(exc.value)


async def test_chat_json_network_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    adapter = _adapter(handler)
    with pytest.raises(ModelProviderError):
        await adapter.chat_json([ChatMessage(role="user", content="hi")])


async def test_chat_json_malformed_structure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    adapter = _adapter(handler)
    with pytest.raises(ModelParseError):
        await adapter.chat_json([ChatMessage(role="user", content="hi")])


def _text_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


async def test_chat_text_returns_raw_without_response_format():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _text_response("# AI 眼镜实测\n\n- 防蓝光有效")

    adapter = _adapter(handler)
    out = await adapter.chat_text([ChatMessage(role="user", content="待整理的文本")])
    assert out == "# AI 眼镜实测\n\n- 防蓝光有效"  # 原文返回，不做 JSON 解析
    assert "response_format" not in captured["body"]  # 文本模式不强制 json_object
    assert captured["body"]["model"] == "qwen3.8-flash"


async def test_chat_text_empty_content_raises_parse_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return _text_response("")

    adapter = _adapter(handler)
    with pytest.raises(ModelParseError):
        await adapter.chat_text([ChatMessage(role="user", content="hi")])


async def test_chat_text_not_configured_raises_without_request():
    adapter = DashScopeModelAdapter("https://x/v1", api_key=None, model="m", transport=None)
    with pytest.raises(ModelNotConfiguredError):
        await adapter.chat_text([ChatMessage(role="user", content="hi")])


async def test_not_configured_raises_without_request():
    adapter = DashScopeModelAdapter("https://x/v1", api_key=None, model="m", transport=None)
    assert adapter.is_configured() is False
    with pytest.raises(ModelNotConfiguredError):
        await adapter.chat_json([ChatMessage(role="user", content="hi")])


def test_prompt_templates_force_json_and_scope():
    class FakeContent:
        content_id = "xhs:1"
        platform = "xhs"
        title = None
        text = "咖啡能提神"

    msgs = build_extract_messages(FakeContent())
    assert msgs[0].role == "system"
    assert "json_object" not in msgs[0].content  # response_format 由适配层下
    assert msgs[-1].role == "user"
    assert "xhs:1" in msgs[-1].content

    # judge prompt 只允许候选里的 content_id
    fake_candidate = type("C", (), {"content_id": "xhs:2", "title": "t", "text": "咖啡因有害"})
    jmsgs = build_judge_messages(
        type("Claim", (), {"claim_id": "c", "content_id": "xhs:1", "text": "咖啡能提神", "claim_type": None})(),
        [fake_candidate],
    )
    assert "xhs:2" in jmsgs[-1].content
    assert "不得编造" in jmsgs[0].content
