"""lc.py 触点的双后端语义锁：真 LC 与 shim（_Lambda）的 |/ainvoke/invoke 行为一致。"""
from __future__ import annotations

import asyncio

import pytest

from app.services import lc
from app.services.lc import RunnableLambda


def test_real_backend_reports_langchain():
    assert lc.LC_BACKEND == "langchain-core"
    assert lc.LANGCHAIN_AVAILABLE is True
    assert lc.LC_TRACING is False


def test_real_backend_env_forced_off():
    import os

    assert os.environ["LANGCHAIN_TRACING_V2"] == "false"
    assert os.environ["LANGSMITH_TRACING"] == "false"
    assert os.environ["LANGCHAIN_TRACING"] == "false"


async def test_real_composes_sync_and_async():
    a = RunnableLambda(lambda x: x + 1)
    b = RunnableLambda(lambda x: x * 10)
    assert await (a | b).ainvoke(4) == 50

    async def slow(x):
        await asyncio.sleep(0)
        return x + 100

    assert await (a | RunnableLambda(slow)).ainvoke(1) == 102


def test_real_sync_invoke_on_async_raises_typeerror():
    async def slow(x):
        return x

    with pytest.raises(TypeError):
        RunnableLambda(slow).invoke(1)


async def test_shim_composes_sync_and_async():
    from app.services.lc import _Lambda

    a = _Lambda(lambda x: x + 1)
    b = _Lambda(lambda x: x * 10)
    assert await (a | b).ainvoke(4) == 50

    async def slow(x):
        await asyncio.sleep(0)
        return x + 100

    assert await (a | _Lambda(slow)).ainvoke(1) == 102


def test_shim_sync_invoke_on_async_raises_typeerror():
    from app.services.lc import _Lambda

    async def slow(x):
        return x

    with pytest.raises(TypeError):
        _Lambda(slow).invoke(1)


async def test_shim_wraps_plain_callable_in_pipe():
    from app.services.lc import _Lambda

    a = _Lambda(lambda x: x + 1)
    assert await (a | (lambda x: x * 2)).ainvoke(3) == 8


def test_document_contract_consistent():
    d = lc.Document(page_content="正文", metadata={"qa_id": "x"})
    assert d.page_content == "正文" and d.metadata["qa_id"] == "x"


def test_exports_surface():
    for name in ("RunnableLambda", "Document", "BaseRetriever", "LANGCHAIN_AVAILABLE", "LC_BACKEND"):
        assert name in lc.__all__
