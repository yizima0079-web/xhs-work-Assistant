"""采集编排：限速 -> 调用适配器 -> 错误分类/重试 -> 三层落盘 -> 去重 -> 审计。

状态机（手册 §4.4）：queued -> running -> success | partial | blocked | failed | cancelled。
重试仅限瞬态网络错误；auth/风控硬失败直接熔断 blocked。
"""
from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from app.adapters.base import CollectorAdapter, is_breaker
from app.config import Settings
from app.domain.enums import ErrorCategory, RunStatus, TargetType
from app.domain.models import CollectionEvent, CollectRequest, RawAsset
from app.repositories.base import (
    ContentRepository,
    EventRepository,
    RawAssetRepository,
    RunRepository,
)
from app.domain.models import Content
from app.services.media import download_cover
from app.services.normalizer import normalize_rows
from app.services.redact import redact_text
from app.services.throttle import Throttle


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PlanStep:
    kind: str  # search | note | comments | user
    command: str
    args: list[str]
    source_url: str | None


class CollectorService:
    def __init__(
        self,
        adapter: CollectorAdapter,
        throttle: Throttle,
        run_repo: RunRepository,
        event_repo: EventRepository,
        content_repo: ContentRepository,
        raw_repo: RawAssetRepository,
        settings: Settings,
    ):
        self.adapter = adapter
        self.throttle = throttle
        self.run_repo = run_repo
        self.event_repo = event_repo
        self.content_repo = content_repo
        self.raw_repo = raw_repo
        self.settings = settings
        self._sem = asyncio.Semaphore(settings.global_concurrency)
        self._cancel: set[str] = set()
        self._current_run: str | None = None
        self.tool_version = "opencli@1.8.8"

    async def cancel(self, run_id: str) -> None:
        """标记取消；若取消的正是当前在跑的任务，连它的子进程一起终止。

        否则取消要等命令自己撞上硬超时才生效，期间 node 还占着 Browser Bridge 会话。
        """
        self._cancel.add(run_id)
        if self._current_run != run_id:
            return
        terminate = getattr(self.adapter, "terminate_active", None)
        if terminate is not None:
            await terminate()

    async def execute(self, run_id: str) -> None:
        try:
            async with self._sem:
                await self._run(run_id)
        except Exception as exc:  # 兜底，避免后台任务静默崩溃
            run = await self.run_repo.get(run_id)
            if run and run.status not in (RunStatus.CANCELLED, RunStatus.BLOCKED):
                run.status = RunStatus.FAILED
                run.error_code = "INTERNAL"
                run.error_category = ErrorCategory.PARSE
                run.finished_at = _now()
                await self.run_repo.update(run)
            await self._event(run_id, "error", ErrorCategory.PARSE, "INTERNAL", str(exc), 999)

    async def _run(self, run_id: str) -> None:
        run = await self.run_repo.get(run_id)
        if run is None or run.status == RunStatus.CANCELLED:
            return

        # 熔断检查（人工确认前不再执行该平台任务）
        if self.throttle.breaker.is_open(run.platform):
            run.status = RunStatus.BLOCKED
            run.error_code = "CIRCUIT_OPEN"
            run.error_category = ErrorCategory.RATE_LIMIT
            run.finished_at = _now()
            await self.run_repo.update(run)
            await self._event(run_id, "warn", ErrorCategory.RATE_LIMIT, "CIRCUIT_OPEN",
                              "熔断开启，人工确认后恢复", 0)
            return

        run.status = RunStatus.RUNNING
        run.started_at = _now()
        # 工具版本进 Run（手册 §4.3 / §13.1）：同一目标跨版本的结果差异可回溯
        run.tool_version = getattr(self.adapter, "tool_version", None) or self.tool_version
        await self.run_repo.update(run)

        self._current_run = run_id
        try:
            # 令牌桶限速（每平台）
            await self.throttle.acquire(run.platform)

            request = CollectRequest(
                platform=run.platform,
                target_type=run.target_type,
                target=run.target,
                max_items=run.max_items,
                request_id=run.request_id,
            )
            steps = self._plan(request)

            seq = 0
            found = 0
            saved = 0
            status = RunStatus.SUCCESS
            error_code = None
            error_category = None

            for step in steps:
                if run_id in self._cancel:
                    status = RunStatus.CANCELLED
                    break

                res = await self.adapter.run(step.command, step.args)

                # 取消可能刚把这条命令杀掉：命令的失败不该盖掉「任务已取消」这个事实
                if run_id in self._cancel:
                    status = RunStatus.CANCELLED
                    break

                # 瞬态错误重试 1 次（不计风控）
                if res.error and res.error.retryable:
                    run.retry_count += 1
                    await self._event(run_id, "warn", res.error.category, res.error.code,
                                      f"瞬时错误，重试 1 次：{res.error.message}", seq)
                    seq += 1
                    res = await self.adapter.run(step.command, step.args)

                if res.error:
                    cat = res.error.category
                    if cat == ErrorCategory.EMPTY:
                        await self._event(run_id, "info", cat, res.error.code, "空结果（0 条）", seq)
                        seq += 1
                        continue
                    error_code = res.error.code
                    error_category = cat
                    if cat == ErrorCategory.RATE_LIMIT:
                        run.rate_limit_signal = 1
                    await self._event(run_id, "error", cat, res.error.code, res.error.message, seq)
                    seq += 1
                    await self.throttle.record_result(run.platform, cat)
                    status = (
                        RunStatus.BLOCKED
                        if is_breaker(cat)
                        else (RunStatus.PARTIAL if saved else RunStatus.FAILED)
                    )
                    break

                # 成功：原始层落盘 + 标准化 + 去重入库
                raw_id = await self._save_raw(run_id, step.kind, res.raw)
                contents = normalize_rows(step.kind, res.rows, source_url=step.source_url)
                found += len(contents)
                for c in contents:
                    c.raw_refs.append(raw_id)
                    await self._download_cover_if_needed(c)
                    if await self.content_repo.upsert(c):
                        saved += 1
                await self.throttle.record_result(run.platform, None)
                await self._event(run_id, "info", None, None, f"{step.kind}: {len(contents)} 条", seq)
                seq += 1

                # keyword 的 search 是轻量候选（无封面/类型/收藏/正文）→ 逐条补抓 note 详情回写
                if step.kind == "search" and contents:
                    seq = await self._enrich_search_details(run_id, contents, seq)

            run.status = status
            run.error_code = error_code
            run.error_category = error_category
            run.items_found = found
            run.items_saved = saved
            run.finished_at = _now()
            await self.run_repo.update(run)

        finally:
            self._current_run = None

    # ---- 内部 ----
    @staticmethod
    def _plan(request: CollectRequest) -> list[PlanStep]:
        if request.target_type == TargetType.KEYWORD:
            return [PlanStep("search", "search", [request.target, "--limit", str(request.max_items)], None)]
        if request.target_type == TargetType.URL:
            return [
                PlanStep("note", "note", [request.target], request.target),
                PlanStep("comments", "comments",
                         [request.target, "--limit", str(min(request.max_items, 50))], request.target),
            ]
        if request.target_type in (TargetType.ACCOUNT, TargetType.POST):
            return [PlanStep("user", "user", [request.target, "--limit", str(request.max_items)], None)]
        return []

    async def _download_cover_if_needed(self, c: Content) -> None:
        """xhs 内容有远程封面时下载到本地（search 行无封面则跳过），失败降级远程 URL。"""
        if c.platform == "xhs" and c.platform_item_id and c.cover_url and not c.cover_local:
            c.cover_local = await download_cover(
                c.cover_url, c.platform_item_id, self.settings.media_dir
            )

    async def _enrich_search_details(self, run_id: str, contents: list, seq: int) -> int:
        """search 结果缺 cover/type/tags/收藏/正文 → 用 note 详情命令补抓，回写同 content_id。

        低频少量：仅对本步 search 返回的条目逐条一次 note 调用；失败记 warn 不阻断采集；
        不计数 found/saved（只做字段补全）。重复 note_id 只补一次。
        """
        seen: set[str] = set()
        for c in contents:
            if run_id in self._cancel:  # 取消后不再逐条补抓 note 详情
                break
            if c.platform != "xhs" or not c.platform_item_id:
                continue
            if c.platform_item_id in seen:
                continue
            seen.add(c.platform_item_id)
            if c.cover_url and c.cover_local:  # 已具备完整详情，跳过
                continue
            url = c.canonical_url
            if not url:
                continue
            res = await self.adapter.run("note", [url])
            if res.error:
                await self._event(run_id, "warn", None, res.error.code,
                                  f"note 详情补抓失败（{c.platform_item_id[:8]}…）：{res.error.message}", seq)
                seq += 1
                continue
            fulls = normalize_rows("note", res.rows, source_url=url)
            if not fulls:
                continue
            full = fulls[0]
            full.raw_refs = list(c.raw_refs)
            await self._download_cover_if_needed(full)
            await self.content_repo.update_content(full)
            await self._event(run_id, "info", None, None,
                              f"note 详情回写：{c.platform_item_id[:8]}…", seq)
            seq += 1
        return seq

    async def _save_raw(self, run_id: str, kind: str, raw_text: str | None) -> str:
        raw_dir = self.settings.raw_dir / run_id
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_id = uuid.uuid4().hex
        data = raw_text or ""
        b = data.encode("utf-8")
        path = raw_dir / f"{kind}-{raw_id}.json"
        path.write_text(data, encoding="utf-8")
        asset = RawAsset(
            id=raw_id, run_id=run_id, kind=kind, mime="application/json",
            sha256=hashlib.sha256(b).hexdigest(), bytes=len(b), storage_path=str(path),
        )
        await self.raw_repo.add(asset)
        return raw_id

    async def _event(
        self, run_id: str, level: str, category: ErrorCategory | None, code: str | None,
        message: str, seq: int,
    ) -> None:
        await self.event_repo.add(
            CollectionEvent(
                run_id=run_id,
                seq=seq,
                level=level,
                category=category,
                code=code,
                message=redact_text(message) or message,
            )
        )
