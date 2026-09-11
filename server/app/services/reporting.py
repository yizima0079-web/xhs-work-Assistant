"""报告服务：多内容样本 → 模型生成 §9.1 payload → 净化 → 落库 + Markdown。

先生成 JSON payload 再渲染 Markdown，避免直接生成不可审计的自然语言。
净化（schemas.report.sanitize_report_payload）强制 content 引用与真实样本求交，
样本不足时趋势强制 uncertain；净化失败抛 ModelParseError(503)，不落空报告。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.adapters.llm import ModelAdapter, ModelNotConfiguredError, ModelParseError
from app.domain.models import Report
from app.repositories.base import ContentRepository, ReviewRepository
from app.schemas.report import (
    ReportPayload,
    ReportScope,
    ReportSchemaError,
    sanitize_report_payload,
)
from app.services.prompt_templates import build_report_messages

_MAX_TEXT_CHARS = 600

_DIRECTION_ZH = {
    "rising": "上升",
    "stable": "平稳",
    "falling": "下行",
    "uncertain": "不确定",
}


def _bullet(items: list[str]) -> str:
    if not items:
        return "- （无）"
    return "\n".join(f"- {i}" for i in items)


def render_markdown(payload: dict[str, Any], title: str = "审核报告", created_at: str = "") -> str:
    """从 payload dict（ReportPayload.model_dump()）渲染可审计 Markdown。"""
    scope = payload.get("scope", {})
    lines: list[str] = [f"# {title}", ""]

    meta = [f"报告 {payload.get('report_id', '')}"]
    if payload.get("analysis_version"):
        meta.append(f"分析版本 {payload['analysis_version']}")
    if created_at:
        meta.append(created_at)
    lines.append("> " + " · ".join(meta))
    lines.append("")

    lines.append("## 范围")
    platforms = scope.get("platforms") or []
    cids = scope.get("content_ids") or []
    time_range = scope.get("time_range") or {}
    lines.append(f"- 平台：{', '.join(platforms) if platforms else '（无）'}")
    lines.append(f"- 内容样本：{len(cids)} 条")
    for cid in cids:
        lines.append(f"  - `{cid}`")
    if time_range:
        lines.append(f"- 时间窗：{json.dumps(time_range, ensure_ascii=False)}")
    lines.append("")

    lines.append("## 摘要")
    lines.append(payload.get("executive_summary") or "（无）")
    lines.append("")

    patterns = payload.get("viral_patterns") or []
    if patterns:
        lines.append("## 爆点模式")
        for i, p in enumerate(patterns, 1):
            lines.append(f"### {i}. {p.get('pattern', '')}")
            lines.append(f"- 置信度：{float(p.get('confidence', 0.0)):.0%}")
            lines.append("- 支撑内容：")
            lines.append(_bullet([f"`{cid}`" for cid in p.get("evidence_content_ids", [])]))
            if p.get("counterexamples"):
                lines.append("- 反例：")
                lines.append(_bullet([f"`{cid}`" for cid in p["counterexamples"]]))
            lines.append("")

    style = payload.get("presentation_style") or {}
    style_sections = [
        ("视觉", style.get("visual")),
        ("文案", style.get("text")),
        ("视频", style.get("video")),
        ("互动", style.get("interaction")),
    ]
    if any(items for _, items in style_sections):
        lines.append("## 表现风格")
        for label, items in style_sections:
            if items:
                lines.append(f"### {label}")
                lines.append(_bullet(items))
        lines.append("")

    persona = payload.get("account_persona") or {}
    if persona.get("hypotheses") or persona.get("evidence"):
        lines.append("## 账号人设（假设）")
        lines.append("- 假设：")
        lines.append(_bullet(persona.get("hypotheses", [])))
        lines.append("- 支撑内容：")
        lines.append(_bullet([f"`{cid}`" for cid in persona.get("evidence", [])]))
        lines.append(f"- 置信度：{float(persona.get('confidence', 0.0)):.0%}")
        lines.append("")

    trend = payload.get("trend_direction") or {}
    if trend:
        lines.append("## 趋势")
        lines.append(_bullet([f"话题：{t}" for t in trend.get("topics", [])]))
        direction = _DIRECTION_ZH.get(trend.get("direction", "uncertain"), "不确定")
        lines.append(f"- 方向：{direction}")
        if trend.get("window"):
            lines.append(f"- 窗口：{trend['window']}")
        lines.append("")

    limitations = payload.get("limitations") or []
    if limitations:
        lines.append("## 局限")
        lines.append(_bullet(limitations))
        lines.append("")

    source_refs = payload.get("source_refs") or []
    if source_refs:
        lines.append("## 引用来源")
        lines.append(_bullet(source_refs))
        lines.append("")

    lines.append("## 审计原文")
    lines.append("```json")
    lines.append(json.dumps(payload, ensure_ascii=False, indent=2))
    lines.append("```")
    return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReportService:
    """对一组 content 生成手册 §9.1 报告并落库。"""

    def __init__(
        self,
        review_repo: ReviewRepository,
        content_repo: ContentRepository,
        llm: ModelAdapter | None,
    ):
        self.review_repo = review_repo
        self.content_repo = content_repo
        self.llm = llm

    async def build_report(
        self, content_ids: list[str], title: str = "审核报告", app_visible_only: bool = False
    ) -> Report:
        """`app_visible_only`（app 通道）下，只要入参里有已被 app 隐藏的内容就 404。

        不是「跳过那一条」——本函数会把每条内容的 `title` 与 `text[:600]` 逐字拼进
        prompt，产出的报告里就带着它的正文。跳过会让调用方以为报告覆盖了 N 条实际
        只覆盖 N-1 条（静默缩水，计数器对不上）；直接拒绝才是诚实的。
        """
        if self.llm is None or not self.llm.is_configured():
            raise ModelNotConfiguredError("服务端未配置模型 API key，无法生成报告")

        contents = []
        for cid in content_ids:
            content = await self.content_repo.get(cid, app_visible_only=app_visible_only)
            if content is None:
                raise KeyError(f"内容不存在: {cid}")
            contents.append(content)

        report_id = uuid.uuid4().hex
        stats = self._build_scope_stats(contents)
        raw = await self.llm.chat_json(build_report_messages(stats))

        scope = ReportScope(
            platforms=sorted({c.platform for c in contents}),
            content_ids=[c.content_id for c in contents],
            time_range=self._time_range(contents),
        )
        source_refs = [c.canonical_url for c in contents if c.canonical_url]
        try:
            payload = sanitize_report_payload(
                raw,
                [c.content_id for c in contents],
                report_id=report_id,
                scope=scope,
                source_refs=source_refs,
            )
        except ReportSchemaError as exc:
            raise ModelParseError(f"报告结构不可用: {exc}") from exc

        created_at = _now()
        markdown = render_markdown(payload.model_dump(), title=title, created_at=created_at)
        report = Report(
            report_id=report_id,
            title=title,
            content_ids=[c.content_id for c in contents],
            payload=payload.model_dump(),
            markdown=markdown,
            created_at=created_at,
            schema_version=payload.analysis_version,
        )
        await self.review_repo.add_report(report)
        return report

    def _build_scope_stats(self, contents) -> dict[str, Any]:
        samples = [
            {
                "content_id": c.content_id,
                "platform": c.platform,
                "title": (c.title or "")[:_MAX_TEXT_CHARS],
                "text": (c.text or "")[:_MAX_TEXT_CHARS],
                "author_name": c.author_name,
                "published_at": c.published_at,
                "engagement": c.engagement.model_dump() if c.engagement else {},
            }
            for c in contents
        ]
        return {"count": len(contents), "platforms": sorted({c.platform for c in contents}), "samples": samples}

    @staticmethod
    def _time_range(contents) -> dict[str, str]:
        stamps = [c.published_at for c in contents if c.published_at]
        if not stamps:
            return {}
        return {"from": min(stamps), "to": max(stamps)}
