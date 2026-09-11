"""OpenCLI 采集适配器：把 OpenCLI CLI 当作子进程调用。

契约（已实测）：
- 成功：stdout 裸 JSON（数组/对象），无包裹层。
- 失败：stderr YAML envelope，`error.code` / `error.message` / `error.exitCode`。
- 无全局 --timeout；超时走 OPENCLI_BROWSER_COMMAND_TIMEOUT。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time

import yaml

from app.adapters.base import (
    CollectorAdapter,
    CommandError,
    CommandResult,
    classify_error,
)
from app.config import Settings
from app.domain.enums import ErrorCategory, RunStatus, TargetType
from app.domain.models import (
    CapabilityReport,
    CollectRequest,
    CollectResult,
    HealthResult,
)
from app.services.normalizer import normalize_rows
from app.services.redact import redact_text

logger = logging.getLogger("datapp.opencli")

# kill 之后等子进程退出的宽限时间（秒）
_TERMINATE_GRACE_SECONDS = 5.0


def _error_dict(res: CommandResult) -> dict:
    e = res.error
    return {
        "code": e.code if e else "UNKNOWN",
        "message": e.message if e else "",
        "category": (e.category.value if e and e.category else "parse"),
        "retryable": bool(e.retryable if e else False),
    }


class OpenCLIAdapter(CollectorAdapter):
    def __init__(self, settings: Settings):
        self._s = settings
        # 正在跑的子进程（全局并发=1，通常 0/1 个）：取消任务时要连它一起收掉
        self._active: set[asyncio.subprocess.Process] = set()

    @property
    def tool_version(self) -> str:
        """写进 Run.tool_version：按工具版本复现采集差异（手册 §4.3 / §13.1）。"""
        return f"opencli@{self._s.opencli_version}"

    # ---- 底层单次调用 ----
    async def run(self, command: str, args: list[str]) -> CommandResult:
        """site 子命令（xiaohongshu <cmd> … -f json），供采集契约调用。"""
        return await self._exec(["xiaohongshu", command, *args, "-f", "json"])

    async def _exec(self, args: list[str]) -> CommandResult:
        """任意 OpenCLI 子进程调用：`<node> <main> <args…>`，解析 JSON/错误 envelope。"""
        cmd = [self._s.opencli_node, str(self._s.opencli_main), *args]
        # 进日志/事件之前先脱敏：含 xsec_token 的 URL 不得外流（AGENTS.md）
        label = redact_text(" ".join(args[:3])) or ""
        env = os.environ.copy()
        if self._s.opencli_profile:
            env["OPENCLI_PROFILE"] = self._s.opencli_profile
        # 强制覆盖而非 setdefault：os.environ 里可能残留同名的 OPENCLI_BROWSER_COMMAND_TIMEOUT
        # （.env 里就曾有一个无 DATAPP_ 前缀的孤立同名键），setdefault 会让它压过服务端配置，
        # 表现成「改了 DATAPP_OPENCLI_COMMAND_TIMEOUT 却不生效」这种查半天的怪事。
        # 浏览器搜索的冷启动（首次唤起会话）实测可超 60s，60 会把冷态首跑直接判死。
        env["OPENCLI_BROWSER_COMMAND_TIMEOUT"] = str(self._s.opencli_command_timeout)
        # 连接预算单独给：扩展/daemon 不在线时让 OpenCLI 快速认输，
        # 不必用整个命令超时去等一个根本连不上的会话。
        env["OPENCLI_BROWSER_CONNECT_TIMEOUT"] = str(self._s.opencli_connect_timeout)

        # 硬超时比内层宽限一段，让 OpenCLI 自己的超时错误先回来（信息更具体）；
        # 越过硬超时即视为子进程失联，直接终止，不留活口。
        hard_timeout = self._s.opencli_command_timeout + self._s.opencli_timeout_grace
        start = time.monotonic()
        proc: asyncio.subprocess.Process | None = None
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=str(self._s.opencli_cwd),
            )
            self._active.add(proc)
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=hard_timeout
            )
        except asyncio.TimeoutError:
            await self._terminate(proc)
            elapsed = time.monotonic() - start
            logger.warning(
                "opencli %s 超时 %.1fs（硬上限 %ss），已终止子进程", label, elapsed, hard_timeout
            )
            return CommandResult(
                command=label,
                error=CommandError(
                    "TIMEOUT",
                    f"{label} 超过 {hard_timeout}s 未返回（已等 {elapsed:.1f}s），子进程已终止："
                    "Bridge 会话卡住或冷启动超预算；确认浏览器与扩展在线后重试",
                    category=ErrorCategory.TIMEOUT,
                    retryable=False,
                ),
            )
        except asyncio.CancelledError:
            # 取消必须连子进程一起收：残留的 node 还挂在 Browser Bridge 会话上，
            # 会占着 tab lease 干扰后续采集。
            await self._terminate(proc)
            raise
        except FileNotFoundError as exc:
            return CommandResult(
                command=label,
                error=CommandError(
                    "CONFIG",
                    f"opencli not found: {exc}",
                    category=ErrorCategory.PARSE,
                    retryable=False,
                ),
            )
        except OSError as exc:
            return CommandResult(
                command=label,
                error=CommandError(
                    "PROCESS_START_FAILED",
                    f"opencli subprocess unavailable: {exc}",
                    category=ErrorCategory.NETWORK,
                    retryable=False,
                ),
            )
        finally:
            if proc is not None:
                self._active.discard(proc)

        duration_ms = int((time.monotonic() - start) * 1000)
        stdout = stdout_b.decode("utf-8", "replace")
        stderr = stderr_b.decode("utf-8", "replace")

        if proc.returncode == 0:
            rows = self._parse_stdout(stdout)
            if rows is None:
                return CommandResult(
                    command=label,
                    raw=stdout,
                    exit_code=0,
                    duration_ms=duration_ms,
                    error=CommandError(
                        "PARSE", "non-JSON stdout", category=ErrorCategory.PARSE, retryable=False
                    ),
                )
            return CommandResult(
                command=label, rows=rows, raw=stdout, exit_code=0, duration_ms=duration_ms
            )

        error = self._parse_stderr(stderr, proc.returncode)
        # 结构化失败日志：任务事件表之外，进程日志也能直接定位风控/环境问题
        logger.warning(
            "opencli %s 失败 code=%s category=%s exit=%s %dms",
            label, error.code, error.category.value, proc.returncode, duration_ms,
        )
        return CommandResult(
            command=label,
            raw=stdout,
            error=error,
            exit_code=proc.returncode,
            duration_ms=duration_ms,
        )

    async def _terminate(self, proc: asyncio.subprocess.Process | None) -> None:
        """终止子进程并等它真的退出（幂等，对已退出进程调用无副作用）。"""
        if proc is None or proc.returncode is not None:
            return
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            return
        try:
            await asyncio.wait_for(proc.wait(), timeout=_TERMINATE_GRACE_SECONDS)
        except (asyncio.TimeoutError, ProcessLookupError, OSError):
            logger.warning("opencli 子进程被 kill 后仍未退出（pid=%s）", proc.pid)

    async def terminate_active(self) -> int:
        """终止当前在跑的命令，返回终止个数。

        全局并发=1，所以至多命中一个；由 CollectorService.cancel 在取消的正是当前
        任务时调用，避免取消一个排队任务却误杀另一个正在跑的命令。
        """
        active = [p for p in self._active if p.returncode is None]
        for proc in active:
            await self._terminate(proc)
        return len(active)


    # ---- 浏览器连接（Browser Bridge）----
    BROWSER_SESSION = "datapp"
    XHS_HOME_URL = "https://www.xiaohongshu.com/"

    async def browser_list_tabs(self, session: str = BROWSER_SESSION) -> CommandResult:
        """列出 Browser Bridge session 内标签页（每行含 url/title/page/index/active）。"""
        return await self._exec(["browser", session, "tab", "list"])

    async def browser_open_page(
        self,
        session: str = BROWSER_SESSION,
        url: str = XHS_HOME_URL,
        window: str = "foreground",
    ) -> CommandResult:
        """在 Browser Bridge session 打开 URL（建立/复用连接），返回 page targetId 等。"""
        return await self._exec(["browser", session, "open", url, "--window", window])

    @staticmethod
    def _parse_stdout(stdout: str) -> list[dict] | None:
        text = stdout.strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        return [{"value": data}]

    @staticmethod
    def _parse_stderr(stderr: str, exit_code: int) -> CommandError:
        try:
            env = yaml.safe_load(stderr)
        except yaml.YAMLError:
            env = None
        if isinstance(env, dict) and isinstance(env.get("error"), dict):
            e = env["error"]
            code = str(e.get("code") or "UNKNOWN")
            message = str(e.get("message") or stderr.strip())
            category, retryable = classify_error(code, message)
            return CommandError(
                code,
                message,
                exit_code=int(e.get("exitCode") or exit_code),
                category=category,
                retryable=retryable,
            )
        # 非 envelope（混入日志等）兜底
        message = stderr.strip() or f"exit code {exit_code}"
        category, retryable = classify_error("UNKNOWN", message)
        return CommandError("UNKNOWN", message, exit_code=exit_code, category=category, retryable=retryable)

    # ---- 契约方法 ----
    async def health_check(self) -> HealthResult:
        res = await self.run("whoami", [])
        if res.ok and res.rows:
            row = res.rows[0]
            return HealthResult(
                ok=True,
                logged_in=bool(row.get("logged_in")),
                username=row.get("username"),
                profile=self._s.opencli_profile,
                detail="whoami ok",
            )
        if res.error:
            return HealthResult(
                ok=False,
                logged_in=False,
                profile=self._s.opencli_profile,
                detail=f"{res.error.code}: {res.error.message}",
            )
        return HealthResult(ok=False, logged_in=False, profile=self._s.opencli_profile, detail="whoami empty")

    def get_capabilities(self) -> CapabilityReport:
        return CapabilityReport(
            platform="xhs",
            commands=["search", "note", "comments", "feed", "user", "whoami"],
            target_types=[TargetType.URL, TargetType.KEYWORD, TargetType.ACCOUNT],
        )

    async def collect(self, request: CollectRequest) -> CollectResult:
        """单次采集（无重试/限速/落盘），返回标准化 items + 错误。"""
        items: list = []
        errors: list[dict] = []

        if request.target_type == TargetType.KEYWORD:
            res = await self.run("search", [request.target, "--limit", str(request.max_items)])
            if res.ok:
                items = normalize_rows("search", res.rows)
            else:
                errors.append(_error_dict(res))

        elif request.target_type == TargetType.URL:
            res = await self.run("note", [request.target])
            if res.ok:
                items = normalize_rows("note", res.rows, source_url=request.target)
            else:
                errors.append(_error_dict(res))

        elif request.target_type in (TargetType.ACCOUNT, TargetType.POST):
            res = await self.run("user", [request.target, "--limit", str(request.max_items)])
            if res.ok:
                items = normalize_rows("user", res.rows)
            else:
                errors.append(_error_dict(res))

        else:
            errors.append({"code": "ARGUMENT", "message": f"unsupported target_type {request.target_type}", "category": "argument", "retryable": False})

        if not items and errors:
            status = RunStatus.FAILED
        elif errors:
            status = RunStatus.PARTIAL
        else:
            status = RunStatus.SUCCESS
        return CollectResult(
            run_id=request.request_id or "",
            status=status,
            items=items,
            items_found=len(items),
            errors=errors,
        )
