"""测试基座：临时 SQLite + FakeAdapter + fixture 加载。"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.adapters.base import CommandResult
from app.config import settings
from app.db import init_db
from app.domain.models import CapabilityReport, HealthResult

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_configure(config):
    # Windows %TEMP% 权限问题：把 pytest 临时基目录收到项目内 F:\datapp\.tmp\pytest。
    # conftest 位于 server/tests/ -> 上三级 = F:\datapp。
    root = Path(__file__).resolve().parents[2]
    override = os.environ.get("DATAPP_PYTEST_BASETEMP")
    candidates = (
        [Path(override)]
        if override
        else [root / ".tmp" / "pytest", root / ".tmp" / "pytest-fallback"]
    )
    for candidate in candidates:
        if _is_usable_base(candidate):
            config.option.basetemp = str(candidate)
            return
    # 一个都不可用就交给 pytest 自己的默认值（并说出来），
    # 而不是把 None 写成 basetemp 去污染报错信息。
    print(f"[conftest] 无可用 basetemp 目录，改用 pytest 默认临时目录；已试 {[str(c) for c in candidates]}")


def _is_usable_base(candidate: Path) -> bool:
    """目录要能建、能写、还要能**枚举**。

    历史会话可能把 .tmp/pytest 留成「可写但不可枚举」（pytest 在 session 结束阶段会
    iterdir，撞上就 PermissionError 挂掉，报错还落在 shutil），所以三项一起探。
    """
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write-probe"
        probe.write_text("", encoding="utf-8")
        list(candidate.iterdir())
        probe.unlink()
    except OSError:
        return False
    return True


@pytest.fixture(autouse=True)
def _no_real_pacing(monkeypatch):
    """测试不做真实限速等待。

    生产默认 10s/条（AGENTS.md 的 5-30s 区间），建多个 run 的用例会白等几十秒；
    限速算法本身由 test_throttle.py 直接测 TokenBucket，不依赖这里。
    """
    monkeypatch.setattr(settings, "rate_per_second", 10000.0)


class FakeAdapter:
    """按 command 名返回预设 CommandResult，记录调用，不触真实 OpenCLI。"""

    def __init__(self, results: dict[str, CommandResult] | None = None):
        self.results = results or {}
        self.calls: list[tuple[str, list[str]]] = []

    async def run(self, command: str, args: list[str]) -> CommandResult:
        self.calls.append((command, args))
        res = self.results.get(command)
        if res is not None:
            return res
        return CommandResult(command=command, rows=[], raw="[]")

    async def health_check(self) -> HealthResult:
        return HealthResult(ok=True, logged_in=True, username="test", profile="p")

    def get_capabilities(self) -> CapabilityReport:
        return CapabilityReport(platform="xhs", commands=["search"], target_types=[])


class FakeModelAdapter:
    """按序吐预设的模型适配器，记录每次调用消息，全程离线。

    chat_json 消费 dict 响应；chat_text 消费 str（若塞入 dict 则取其 text/content 字段）。
    """

    def __init__(self, responses: list | None = None):
        self._queue = list(responses or [])
        self.calls: list[list] = []

    def is_configured(self) -> bool:
        return True

    async def chat_json(self, messages) -> dict:
        self.calls.append(messages)
        if not self._queue:
            return {}
        return self._queue.pop(0)

    async def chat_text(self, messages) -> str:
        self.calls.append(messages)
        if not self._queue:
            return ""
        item = self._queue.pop(0)
        if isinstance(item, str):
            return item
        if isinstance(item, dict):
            return item.get("text") or item.get("content") or ""
        return str(item)


@pytest.fixture
def make_adapter():
    def _make(results=None):
        return FakeAdapter(results)
    return _make


@pytest.fixture
def make_llm():
    """构造按序返回 JSON 的假模型适配器。空 responses 时 chat_json 返回 {}。"""
    def _make(responses=None):
        return FakeModelAdapter(responses)
    return _make


@pytest.fixture
def load_fixture():
    def _load(name: str):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return _load


@pytest.fixture
async def init_test_db(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "db_path", tmp_path / "test.db")
    monkeypatch.setattr(settings, "raw_dir", tmp_path / "raw")
    await init_db()
    return settings
