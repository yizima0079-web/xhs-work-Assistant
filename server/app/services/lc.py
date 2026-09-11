"""LangChain 唯一触点：收容全部 `langchain_core` 引用，并提供等价的纯 Python 回退实现。

为什么要有这一层（决策：LangChain 仅作编排层）：
- 现有 chunking / DashScope embedding / SQLite JSON 向量 / Python 余弦检索全部保持自研，
  LangChain 只负责把「检索 → 提示 → 模型 → 解析」四步用 LCEL 串起来。
- langchain_core 装不上（wheel 缺失、平台编译失败）时本模块自动退化为等价 shim，
  链路代码只有一份、业务模块零改动；`LC_BACKEND` 在 /health 可见。

隐私红线：langsmith 是 langchain-core 的传递依赖且具备外发能力，这里**直接赋值**关闭全部追踪开关
（不用 setdefault，防止 .env 反向打开）——采集内容与 prompt 不得离开本机。

表面积刻意压到 3 个符号（RunnableLambda / Document / BaseRetriever），
不用 ChatPromptTemplate（prompt_templates.py 已有纯函数 builder）、不用 StrOutputParser
（要结构化 JSON，复用 adapters/llm.py::_extract_json）、不引入任何 model/vectorstore 封装
（那会架空 ModelAdapter 的 503 语义与 key 脱敏）。
"""
from __future__ import annotations

import inspect
import os
from dataclasses import dataclass, field
from typing import Any, Callable

# 必须在 import langchain_core / langsmith 之前生效：强制关闭追踪。
for _key in ("LANGCHAIN_TRACING_V2", "LANGSMITH_TRACING", "LANGCHAIN_TRACING", "LANGCHAIN_ENDPOINT"):
    os.environ[_key] = "false"

LC_TRACING = False


def _compose(first: "RunnableLambda", second: "RunnableLambda", value: Any) -> Any:
    """shim 用：`a | b` 的异步复合。"""
    mid = first.ainvoke(value)
    if inspect.isawaitable(mid):
        async def _await_then(inner):
            return await second.ainvoke(await inner)

        return _await_then(mid)
    return second.ainvoke(mid)


class _Lambda:
    """LCEL 的最小等价物：支持 `|` 复合与 invoke/ainvoke（协程自动 await）。"""

    def __init__(self, fn: Callable[[Any], Any]) -> None:
        self._fn = fn

    def __or__(self, other: Any) -> "_Lambda":
        if not isinstance(other, _Lambda):
            other = RunnableLambda(other)
        return _Lambda(lambda x, a=self, b=other: _compose(a, b, x))

    def invoke(self, value: Any, **_kw: Any) -> Any:
        out = self._fn(value)
        if inspect.isawaitable(out):
            close = getattr(out, "close", None)
            if callable(close):
                close()
            raise TypeError("链路含异步步骤，请使用 ainvoke()")
        return out

    async def ainvoke(self, value: Any, **_kw: Any) -> Any:
        out = self._fn(value)
        if inspect.isawaitable(out):
            out = await out
        return out


try:  # pragma: no cover - 两条分支各自由环境决定，测试用 monkeypatch 覆盖
    from langchain_core.documents import Document as Document
    from langchain_core.retrievers import BaseRetriever as BaseRetriever
    from langchain_core.runnables import RunnableLambda as RunnableLambda

    LANGCHAIN_AVAILABLE = True
    LC_BACKEND = "langchain-core"
except ImportError:  # pragma: no cover - 回退路径
    LANGCHAIN_AVAILABLE = False
    LC_BACKEND = "fallback"

    @dataclass
    class Document:  # type: ignore[no-redef]
        page_content: str
        metadata: dict[str, Any] = field(default_factory=dict)

    class BaseRetriever:  # type: ignore[no-redef]
        """真 LC 下是 pydantic 基类；shim 下只需可继承。"""

    def RunnableLambda(fn: Callable[[Any], Any]) -> _Lambda:  # type: ignore[no-redef]
        return _Lambda(fn)


__all__ = [
    "LANGCHAIN_AVAILABLE",
    "LC_BACKEND",
    "LC_TRACING",
    "Document",
    "BaseRetriever",
    "RunnableLambda",
]
