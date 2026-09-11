import os
import subprocess
import sys
from pathlib import Path

from tools.regression_monitor import (
    ROOT,
    SQLITE_PROBE_DB,
    SQLITE_PROBE_SNIPPET,
    changed_files,
    fingerprint,
    load_state,
    save_state,
    server_child_env,
)

SERVER_DIR = Path(__file__).resolve().parents[1]


def test_fingerprint_detects_core_change(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("x = 1", encoding="utf-8")
    before = fingerprint([target], tmp_path)
    target.write_text("x = 2", encoding="utf-8")
    after = fingerprint([target], tmp_path)
    assert changed_files({"fingerprint": before}, after) == ["a.py"]


def test_state_round_trip(tmp_path: Path):
    state = tmp_path / "state.json"
    save_state(state, {"a.py": "hash"})
    assert load_state(state)["fingerprint"] == {"a.py": "hash"}


# ---- 回归监控器自身缺陷回归锁 ----

def test_server_child_env_single_source_under_root_tmp(tmp_path: Path):
    env, basetemp = server_child_env(tmp_path)
    # 缺陷 A 锁：建目录处 == 传给 pytest 的 --basetemp 处，且都在 root/.tmp 下
    assert basetemp == tmp_path / ".tmp" / "pytest-base"
    assert env["TMP"] == str(tmp_path / ".tmp" / "pytest-temp")
    assert env["TEMP"] == env["TMP"]
    assert env["UV_CACHE_DIR"] == str(tmp_path / ".tmp" / "uv-cache")
    assert basetemp.is_dir()
    assert basetemp.relative_to(tmp_path / ".tmp") is not None


def test_sqlite_probe_no_cwd_guess_no_tempfile():
    # 缺陷 B 锁：探针不依赖 cwd 猜 .tmp，也不拼无用 import tempfile
    assert "tempfile" not in SQLITE_PROBE_SNIPPET
    assert "cwd" not in SQLITE_PROBE_SNIPPET
    assert "init_db" in SQLITE_PROBE_SNIPPET
    assert SQLITE_PROBE_DB == ROOT / ".tmp" / "regression-probe.db"


def test_sqlite_probe_runs_offline(tmp_path: Path):
    # 端到端：cwd=server，库路径由 DATAPP_DB_PATH 注入，必须能建表成功
    db = tmp_path / "probe.db"
    env = os.environ.copy()
    env["DATAPP_DB_PATH"] = str(db)
    result = subprocess.run(
        [sys.executable, "-c", SQLITE_PROBE_SNIPPET],
        cwd=SERVER_DIR, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert db.exists()


def test_monitor_source_regressions_locked():
    src = (SERVER_DIR / "tools" / "regression_monitor.py").read_text(encoding="utf-8")
    # 缺陷 C 锁：Windows 前端命令保留 .cmd 后缀
    assert '"npx.cmd"' in src and '"npm.cmd"' in src
    # 缺陷 A 锁：无 server 相对 basetemp 残留；basetemp/env 全部经 server_child_env
    assert '(cwd / ".tmp" / "pytest-base")' not in src
    assert src.count("server_child_env") >= 3
    # 缺陷 B 锁：SQLite 初始化经 SQLITE_PROBE_SNIPPET + DATAPP_DB_PATH 注入
    assert "SQLITE_PROBE_SNIPPET" in src
    assert "DATAPP_DB_PATH" in src
