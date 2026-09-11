"""作品分析服务：单条 content → 同话题候选检索 → 模型归因 → 净化 → JSON+Markdown 落库。

与审核报告不同：对象是单条内容，判定其爆款(viral)/平淡(flat)。可选横向对比依赖
CorpusSearcher 从本地已采语料检索同话题候选做基线（当前为关键词检索，阶段 3 升级向量检索时
只替换候选来源）。净化（schemas.analysis.sanitize_analysis_payload）强制基线 content 引用
与真实候选求交，失败抛 ModelParseError(503) 不落库。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.adapters.llm import ModelAdapter, ModelNotConfiguredError, ModelParseError
from app.domain.models import Analysis, Content
from app.repositories.base import AnalysisRepository, ContentRepository
from app.schemas.analysis import (
    AnalysisPayload,
    AnalysisSchemaError,
    sanitize_analysis_payload,
)
from app.services.prompt_templates import build_analysis_messages
from app.services.searcher import CorpusSearcher

_MAX_CANDIDATES = 8
_MAX_META_CHARS = 300

_VERDICT_ZH = {"viral": "爆款", "flat": "平淡", "uncertain": "不确定"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bullet(items: list[str]) -> str:
    if not items:
        return "- （无）"
    return "\n".join(f"- {i}" for i in items)


def _reason_lines(reasons: list[dict]) -> list[str]:
    lines: list[str] = []
    for i, r in enumerate(reasons, 1):
        lines.append(f"### {i}. {r.get('factor') or '（未命名因子）'}")
        if r.get("evidence"):
            lines.append(f"- 依据：{r['evidence']}")
        lines.append(f"- 置信度：{float(r.get('confidence', 0.0)):.0%}")
        lines.append("")
    return lines


def render_analysis_markdown(
    payload: dict[str, Any], *, content: Content, created_at: str = ""
) -> str:
    """从 payload dict（AnalysisPayload.model_dump()）渲染可审计 Markdown。"""
    lines: list[str] = ["# 作品分析", ""]

    meta = [f"分析 {payload.get('analysis_id', '')}", f"内容 {payload.get('content_id', '')}"]
    if payload.get("schema_version"):
        meta.append(f"分析版本 {payload['schema_version']}")
    if created_at:
        meta.append(created_at)
    lines.append("> " + " · ".join(meta))
    lines.append("")

    eng = content.engagement.model_dump() if content.engagement else {}
    eng_line = " / ".join(
        f"{k}:{v}" for k, v in eng.items() if v is not None
    ) or "（无互动数据）"
    ctype = content.content_type.value if content.content_type else "（未知）"
    lines.append("## 内容快照")
    lines.append(f"- 标题：{content.title or '（无）'}")
    lines.append(f"- 作者：{content.author_name or '（未知）'} · 类型：{ctype}")
    if content.published_at:
        lines.append(f"- 发布时间:{content.published_at[:10]}")
    lines.append(f"- 互动（点赞/评论/转发/收藏）：{eng_line}")
    if content.tags:
        lines.append(f"- 标签：{' '.join(content.tags)}")
    text = (content.text or "").strip()
    if text:
        lines.append("- 正文摘录：")
        lines.append("\n".join(f"  {ln}" for ln in text[:200].splitlines()[:6]))
    lines.append("")

    # 补充视角排在最前：先声明本次是「非标准分析」，再给判定，便于审计口径
    focus = (payload.get("focus") or "").strip()
    if focus:
        lines.append("## 补充关注点（用户指定）")
        lines.append(f"> {focus}")
        lines.append("")
        lines.append("> 该关注点仅在标准分析框架内补充观察侧重，不改变判定口径与 JSON 结构。")
        lines.append("")

    verdict = payload.get("verdict", "uncertain")
    lines.append("## 判定")
    label = _VERDICT_ZH.get(verdict, "不确定")
    lines.append(f"- 结论：**{label}**（置信度 {float(payload.get('confidence', 0.0)):.0%}）")
    if payload.get("summary"):
        lines.append(f"- 一句话结论：{payload['summary']}")
    lines.append("")

    topic = (payload.get("topic") or "").strip()
    if topic:
        lines.append("## 话题归类")
        lines.append(topic)
        lines.append("")

    sections = [("爆款归因", "viral_reasons"), ("平淡归因", "flat_reasons")]
    for title, key in sections:
        reasons = payload.get(key) or []
        if reasons:
            lines.append(f"## {title}")
            lines.extend(_reason_lines(reasons))
            lines.append("")

    hooks = payload.get("hooks") or []
    audience = payload.get("audience") or []
    if hooks:
        lines.append("## 开头钩子")
        lines.append(_bullet(hooks))
        lines.append("")
    if audience:
        lines.append("## 目标人群")
        lines.append(_bullet(audience))
        lines.append("")

    comparison = payload.get("comparison") or {}
    baseline_ids = comparison.get("baseline_content_ids") or []
    if baseline_ids:
        lines.append("## 同话题横向对比")
        lines.append(f"- 基线样本：{len(baseline_ids)} 条")
        lines.append(_bullet([f"`{cid}`" for cid in baseline_ids]))
        for label, key in (("差异点", "differentiators"), ("共性模式", "shared_patterns")):
            items = comparison.get(key) or []
            if items:
                lines.append(f"- {label}：")
                lines.append(_bullet(items))
        lines.append("")

    suggestions = payload.get("suggestions") or []
    if suggestions:
        lines.append("## 复刻/改进建议")
        lines.append(_bullet(suggestions))
        lines.append("")

    limitations = payload.get("limitations") or []
    if limitations:
        lines.append("## 局限")
        lines.append(_bullet(limitations))
        lines.append("")

    lines.append("## 审计原文")
    lines.append("```json")
    lines.append(json.dumps(payload, ensure_ascii=False, indent=2))
    lines.append("```")
    return "\n".join(lines)


def topic_seed(content: Content) -> str:
    """话题种子文本：tags/title 是话题最强信号放前面，正文长文本截断后交 split_keywords 取词。

    提到模块级供 QaDistiller 复用（分析侧同话题检索与蒸馏侧的话题词完全一致）。
    """
    parts = []
    if content.tags:
        parts.append(" ".join(content.tags))
    if content.title:
        parts.append(content.title)
    if content.text:
        parts.append(content.text[:200])
    return "\n".join(parts)[:_MAX_META_CHARS]


class AnalysisService:
    """对单条内容生成爆款/平淡归因分析并落库（JSON+Markdown）。"""

    def __init__(
        self,
        analysis_repo: AnalysisRepository,
        content_repo: ContentRepository,
        llm: ModelAdapter | None,
        searcher: CorpusSearcher,
    ):
        self.analysis_repo = analysis_repo
        self.content_repo = content_repo
        self.llm = llm
        self.searcher = searcher

    async def analyze(
        self, content_id: str, focus: str = "", app_visible_only: bool = False
    ) -> Analysis:
        """标准分析；focus 非空时在既有框架内附加用户指定的观察侧重（不改口径）。

        `app_visible_only`（app 通道）下，已隐藏的内容等同于不存在 → 404（与 app
        的视角一致，也省下一次无意义的模型调用）；对比候选一并收窄，防止隐藏内容
        的标题正文被写进新分析的 `payload.comparison` 里。
        """
        if self.llm is None or not self.llm.is_configured():
            raise ModelNotConfiguredError("服务端未配置模型 API key，无法生成作品分析")

        content = await self.content_repo.get(content_id, app_visible_only=app_visible_only)
        if content is None:
            raise KeyError(f"内容不存在: {content_id}")

        # 横向对比候选：以本内容话题（标题+正文+标签）检索同话题内容，排除自身
        candidates = await self.searcher.find_candidates(
            topic_seed(content),
            exclude_content_id=content_id,
            limit=_MAX_CANDIDATES,
            app_visible_only=app_visible_only,
        )

        analysis_id = uuid.uuid4().hex
        focus = (focus or "").strip()[:500]
        raw = await self.llm.chat_json(build_analysis_messages(content, candidates, focus))
        allowed = [c.content_id for c in candidates]
        try:
            payload = sanitize_analysis_payload(
                raw,
                analysis_id=analysis_id,
                content_id=content_id,
                allowed_baselines=allowed,
            )
        except AnalysisSchemaError as exc:
            raise ModelParseError(f"分析结构不可用: {exc}") from exc

        created_at = _now()
        payload_dict = payload.model_dump()
        payload_dict["focus"] = focus
        markdown = render_analysis_markdown(
            payload_dict, content=content, created_at=created_at
        )
        analysis = Analysis(
            analysis_id=analysis_id,
            content_id=content_id,
            verdict=payload.verdict,
            payload=payload_dict,
            markdown=markdown,
            compared_with=payload.comparison.baseline_content_ids,
            focus=focus,
            created_at=created_at,
            schema_version=payload.schema_version,
        )
        await self.analysis_repo.add(analysis)
        return analysis

    async def get(self, analysis_id: str, app_visible_only: bool = False) -> Analysis | None:
        """`app_visible_only` 由 app 通道传 True：隐藏内容的分析返回 None → 路由 404。"""
        return await self.analysis_repo.get(analysis_id, app_visible_only=app_visible_only)

    async def list_by_content(
        self, content_id: str, limit: int = 50, offset: int = 0, app_visible_only: bool = False
    ) -> list[Analysis]:
        return await self.analysis_repo.list_by_content(
            content_id, limit=limit, offset=offset, app_visible_only=app_visible_only
        )
