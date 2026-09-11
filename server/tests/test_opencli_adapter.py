"""OpenCLI 适配器：stdout JSON / stderr YAML 解析、错误分类、子进程生命周期。"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.adapters.base import classify_error, is_breaker
from app.adapters.opencli import OpenCLIAdapter
from app.domain.enums import ErrorCategory


def test_parse_stdout_array():
    assert OpenCLIAdapter._parse_stdout('[{"a": 1}]') == [{"a": 1}]


def test_parse_stdout_object_wraps_to_list():
    assert OpenCLIAdapter._parse_stdout('{"a": 1}') == [{"a": 1}]


def test_parse_stdout_empty():
    assert OpenCLIAdapter._parse_stdout("   ") == []


def test_parse_stdout_invalid():
    assert OpenCLIAdapter._parse_stdout("not json") is None


def test_parse_stderr_envelope():
    err = OpenCLIAdapter._parse_stderr(
        "ok: false\nerror:\n  code: SECURITY_BLOCK\n  message: blocked by risk control\n  exitCode: 1\n",
        1,
    )
    assert err.code == "SECURITY_BLOCK"
    assert err.category == ErrorCategory.RATE_LIMIT
    assert err.retryable is False
    assert err.exit_code == 1


def test_parse_stderr_fallback():
    err = OpenCLIAdapter._parse_stderr("some raw error text", 2)
    assert err.code == "UNKNOWN"
    assert err.category == ErrorCategory.PARSE


def test_classify_error():
    assert classify_error("command_result_unknown", "Browser connection dropped after navigate") == (
        ErrorCategory.NETWORK,
        True,
    )
    assert classify_error("COMMAND_EXEC", "Browser connection dropped ... may have completed") == (
        ErrorCategory.NETWORK,
        True,
    )
    assert classify_error("TIMEOUT", "x") == (ErrorCategory.TIMEOUT, False)
    assert classify_error("AUTH_REQUIRED", "x") == (ErrorCategory.AUTH, False)
    assert classify_error("SECURITY_BLOCK", "x") == (ErrorCategory.RATE_LIMIT, False)
    assert classify_error("ARGUMENT", "x") == (ErrorCategory.ARGUMENT, False)
    assert classify_error("EMPTY_RESULT", "x") == (ErrorCategory.EMPTY, False)
    assert classify_error("COMMAND_EXEC", "unknown layout") == (ErrorCategory.PARSE, False)


def test_is_breaker():
    assert is_breaker(ErrorCategory.AUTH)
    assert is_breaker(ErrorCategory.RATE_LIMIT)
    assert not is_breaker(ErrorCategory.NETWORK)
    assert not is_breaker(ErrorCategory.TIMEOUT)


# ---- 错误分类：风控 / 环境不可用 / 适配器漂移 ----

def test_classify_error_treats_risk_signals_as_breaker():
    """平台把风控藏在文案里：码或文案命中都要熔断且禁止重试（AGENTS.md）。"""
    for code, msg in [
        ("COMMAND_EXEC", "redirected to website-login/error?error_code=300017"),
        ("COMMAND_EXEC", "300031"),
        ("COMMAND_EXEC", "HTTP 429 Too Many Requests"),
        ("COMMAND_EXEC", "403 Forbidden"),
        ("COMMAND_EXEC", "当前访问存在安全限制"),
        ("COMMAND_EXEC", "请完成验证码 / 滑块校验"),
        ("SECURITY_BLOCK", "blocked by risk control"),
    ]:
        category, retryable = classify_error(code, msg)
        assert category == ErrorCategory.RATE_LIMIT, msg
        assert retryable is False
        assert is_breaker(category), msg


def test_classify_error_note_id_containing_403_is_not_risk():
    """数字码要带词边界：note_id 之类的十六进制串里出现 403 不算风控。"""
    category, _ = classify_error("COMMAND_EXEC", "note 5f4030a1b2c3d4e5f6071829 not found")
    assert category == ErrorCategory.PARSE


def test_classify_error_env_unavailable_is_not_retryable():
    category, retryable = classify_error(
        "DAEMON_UNAVAILABLE", "Extension is not connected to the daemon"
    )
    assert category == ErrorCategory.NETWORK
    assert retryable is False  # 重试也只是再失败一次，得先把浏览器/扩展拉起来


def test_classify_error_layout_drift_is_parse_not_breaker():
    category, retryable = classify_error(
        "COMMAND_EXEC", "search filter layout did not match (ambiguous_option)"
    )
    assert category == ErrorCategory.PARSE
    assert retryable is False
    assert not is_breaker(category)


# ---- 子进程生命周期：超时/取消必须真的收掉 ----

class _FakeProc:
    """记录 kill/wait，用于覆盖 _terminate 的幂等与正常路径。"""

    def __init__(self, returncode=None):
        self.returncode = returncode
        self.pid = 4242
        self.killed = 0
        self.waited = 0

    def kill(self):
        self.killed += 1
        self.returncode = -9

    async def wait(self):
        self.waited += 1
        return self.returncode


def _fake_settings(**overrides):
    """OpenCLIAdapter 只读这几个字段，用轻量对象免去真实 Settings/路径约束。"""
    base = dict(
        opencli_node=sys.executable,
        opencli_main=Path("-c"),  # 让 cmd 变成 `python -c <args[0]>`
        opencli_profile=None,
        opencli_cwd=Path("."),
        opencli_command_timeout=1,
        opencli_connect_timeout=5,
        opencli_timeout_grace=1,
        opencli_version="test",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_tool_version_reflects_configured_version():
    assert OpenCLIAdapter(_fake_settings(opencli_version="9.9.9")).tool_version == "opencli@9.9.9"


async def test_terminate_is_noop_for_exited_process():
    adapter = OpenCLIAdapter(_fake_settings())
    proc = _FakeProc(returncode=0)
    await adapter._terminate(proc)
    assert (proc.killed, proc.waited) == (0, 0)


async def test_terminate_kills_and_reaps():
    adapter = OpenCLIAdapter(_fake_settings())
    proc = _FakeProc()
    await adapter._terminate(proc)
    assert (proc.killed, proc.waited) == (1, 1)


async def _asyncio_subprocess_works() -> bool:
    """能否用 asyncio 创建带管道的子进程。

    受限沙箱（如本仓库的 agent 执行环境）会拒绝 Windows 命名管道：`subprocess.run`
    走匿名管道照样能跑，只有 asyncio 这条路径会被拒 —— 所以必须按 asyncio 的方式探，
    否则探测结果会假阳性。探不通就跳过超时用例，而把它当成产品缺陷。
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "print(1)",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
    except OSError:
        return False
    return proc.returncode == 0


async def test_exec_timeout_kills_child_instead_of_leaving_it():
    """超时不能只 return：残留 node 会继续占着 Browser Bridge 会话。"""
    if not await _asyncio_subprocess_works():
        pytest.skip("当前环境不允许创建带管道的子进程，需在正常环境验证该路径")
    adapter = OpenCLIAdapter(_fake_settings())
    started = time.monotonic()
    res = await adapter._exec(["import time; time.sleep(30)"])
    elapsed = time.monotonic() - started

    assert res.error is not None
    assert res.error.code == "TIMEOUT"
    assert res.error.category == ErrorCategory.TIMEOUT
    assert res.error.retryable is False
    assert elapsed < 10  # 硬超时 1+1=2s 生效，没等满子进程的 30s
    assert adapter._active == set()  # 没有残留活跃子进程
