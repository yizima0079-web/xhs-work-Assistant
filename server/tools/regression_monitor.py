"""核心代码变更监控与分层回归验证。

默认每 120 秒轮询一次；使用 --once 执行单次扫描，便于 CI 和离线测试。
不连接真实平台，不执行采集命令。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATE = ROOT / ".tmp" / "regression-monitor-state.json"
DEFAULT_AUDIT = ROOT / "storage" / "regression-audit.jsonl"

# SQLite 初始化探针：cwd 必须是 server（依赖 app.db 可导入），库路径由
# DATAPP_DB_PATH 环境变量显式注入，绝不依赖 cwd 猜测 .tmp 位置。
SQLITE_PROBE_SNIPPET = "import asyncio; from app.db import init_db; asyncio.run(init_db())"
SQLITE_PROBE_DB = ROOT / ".tmp" / "regression-probe.db"


def server_child_env(root: Path) -> tuple[dict[str, str], Path]:
    """server 子进程环境（单一来源，缺陷 A 修复）。

    返回 (环境变量增补, pytest --basetemp 目录)：TMP/TEMP、UV_CACHE_DIR、
    pytest 临时基目录全部收敛到 root/.tmp 下，规避 Windows %TEMP% 权限问题，
    并保证"建目录处 == 传给 pytest 的 --basetemp 处"。
    """
    tmp = root / ".tmp" / "pytest-temp"
    tmp.mkdir(parents=True, exist_ok=True)
    basetemp = root / ".tmp" / "pytest-base"
    basetemp.mkdir(parents=True, exist_ok=True)
    return {
        "TMP": str(tmp),
        "TEMP": str(tmp),
        "UV_CACHE_DIR": str(root / ".tmp" / "uv-cache"),
    }, basetemp
CORE_GLOBS = (
    "server/app/**/*.py", "server/tests/**/*.py", "server/schema.sql",
    "client/src/**/*", "server/pyproject.toml", "client/package.json",
    "AGENTS.md", "agent.md", "CLAUDE.md",
)
SECRET_RE = re.compile(
    r"(?i)(?:authorization\s*[:=]|bearer\s+[A-Za-z0-9._-]{12,}|"
    r"(?:api[_-]?key|access[_-]?token|secret)\s*[:=]\s*[\"'][^\"']+)"
)
XSEC_RE = re.compile(r"(?i)xsec_token\s*=\s*(?!test\b)|https?://[^\s\"']*xsec_token")


@dataclass
class Check:
    name: str
    command: str
    severity: str
    status: str
    duration_ms: int
    output: str = ""
    recommendation: str = ""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def tracked_files(root: Path = ROOT) -> list[Path]:
    files: set[Path] = set()
    for pattern in CORE_GLOBS:
        candidate = root / pattern
        if candidate.is_file():
            files.add(candidate)
        else:
            files.update(p for p in root.glob(pattern) if p.is_file())
    return sorted(files)


def fingerprint(files: Iterable[Path], root: Path = ROOT) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        result[str(path.relative_to(root))] = digest
    return result


def load_state(path: Path) -> dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(path: Path, current: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fingerprint": current, "updated_at": utc_now()}, ensure_ascii=False, indent=2), encoding="utf-8")


def changed_files(previous: dict[str, object], current: dict[str, str]) -> list[str]:
    old = previous.get("fingerprint", {})
    if not isinstance(old, dict):
        return sorted(current)
    return sorted(k for k in set(old) | set(current) if old.get(k) != current.get(k))


def run_check(
    name: str, command: list[str], severity: str, cwd: Path, extra_env: dict[str, str] | None = None
) -> Check:
    started = time.perf_counter()
    try:
        env = os.environ.copy()
        project_root = cwd.parent if cwd.name == "server" else cwd
        if cwd.name == "server":
            addons, _ = server_child_env(project_root)
            env.update(addons)
        if extra_env:
            env.update(extra_env)
        proc = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=180)
        output = (proc.stdout + "\n" + proc.stderr).strip()[-12000:]
        ok = proc.returncode == 0
    except subprocess.TimeoutExpired as exc:
        output = f"timeout: {exc}"
        ok = False
    except OSError as exc:
        output = repr(exc)
        ok = False
    return Check(name, " ".join(command), severity if not ok else "P3", "pass" if ok else "fail", int((time.perf_counter() - started) * 1000), output, "修复命令失败、启动入口或依赖配置后重新执行。" if not ok else "")


def security_scan(root: Path = ROOT) -> Check:
    started = time.perf_counter()
    hits: list[str] = []
    for path in tracked_files(root):
        if path.suffix not in {".py", ".ts", ".tsx", ".js", ".json", ".md", ".sql", ".toml"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        is_fixture = "tests" in path.parts
        for number, line in enumerate(text.splitlines(), 1):
            if SECRET_RE.search(line) or (not is_fixture and XSEC_RE.search(line)):
                hits.append(f"{path.relative_to(root)}:{number}")
    return Check("安全规则扫描", "内置规则扫描", "P0", "fail" if hits else "pass", int((time.perf_counter() - started) * 1000), "\n".join(hits), "移除凭据并对包含 xsec_token 的 URL 做脱敏，禁止写入代码、日志和报告。" if hits else "")


def run_validation(root: Path = ROOT) -> list[Check]:
    checks: list[Check] = []
    def add(check: Check) -> bool:
        checks.append(check)
        return check.status == "fail" and check.severity == "P0"

    python_files = [str(p.relative_to(root)) for p in (root / "server" / "app").rglob("*.py")]
    if add(run_check("Python 编译", [sys.executable, "-m", "py_compile", *python_files], "P0", root)):
        return checks
    # pytest --basetemp 与建目录同源（server_child_env 收敛到 root/.tmp，缺陷 A 锁）
    _, pytest_base = server_child_env(root)
    add(run_check("后端快速单测", ["uv", "run", "pytest", "-q", "--basetemp", str(pytest_base), "tests/test_throttle.py", "tests/test_normalizer.py"], "P1", root / "server"))
    add(run_check("后端完整测试", ["uv", "run", "pytest", "-q", "--basetemp", str(pytest_base)], "P1", root / "server"))
    add(run_check("前端类型检查", ["npx.cmd", "tsc", "-b"], "P1", root / "client"))
    add(run_check("前端构建检查", ["npm.cmd", "run", "build"], "P1", root / "client"))
    add(run_check("FastAPI 可执行性", [sys.executable, "-c", "from app.main import create_app; assert create_app().title"], "P0", root / "server"))
    # 库路径经 DATAPP_DB_PATH 显式注入，不靠 cwd 猜测（缺陷 B 锁）；不再拼 import tempfile
    add(run_check("SQLite 初始化", [sys.executable, "-c", SQLITE_PROBE_SNIPPET], "P1", root / "server",
                  extra_env={"DATAPP_DB_PATH": str(SQLITE_PROBE_DB)}))
    add(run_check("关键路径性能", [sys.executable, "-c", "import time; from app.services.throttle import Throttle; t=time.perf_counter(); x=Throttle(1000, 1, 3); assert x; assert time.perf_counter()-t < 0.5"], "P2", root / "server"))
    if add(security_scan(root)):
        return checks
    return checks


def write_audit(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def scan_once(state_path: Path = DEFAULT_STATE, audit_path: Path = DEFAULT_AUDIT, root: Path = ROOT) -> dict[str, object]:
    current = fingerprint(tracked_files(root), root)
    previous = load_state(state_path)
    changed = changed_files(previous, current)
    result: dict[str, object] = {"timestamp": utc_now(), "changed_files": changed, "checks": [], "overall": "unchanged" if not changed else "passed"}
    if changed:
        checks = run_validation(root)
        result["checks"] = [asdict(c) for c in checks]
        p0 = next((c for c in checks if c.status == "fail" and c.severity == "P0"), None)
        result["overall"] = "P0" if p0 else ("failed" if any(c.status == "fail" for c in checks) else "passed")
        if p0:
            print(f"[P0] 回归验证中止：{p0.name}\n{p0.output}\n建议：{p0.recommendation}", file=sys.stderr)
    save_state(state_path, current)
    write_audit(audit_path, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="datapp 核心代码回归监控")
    parser.add_argument("--once", action="store_true", help="只扫描一次")
    parser.add_argument("--interval", type=int, default=120, help="轮询秒数，默认 120")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    args = parser.parse_args()
    while True:
        result = scan_once(args.state, args.audit)
        print(f"[{result['timestamp']}] overall={result['overall']} changed={len(result['changed_files'])}")
        if args.once:
            return 1 if result["overall"] == "P0" else 0
        try:
            time.sleep(max(1, args.interval))
        except KeyboardInterrupt:
            print("监控已停止。")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
