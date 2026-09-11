"""报告 payload schema（手册 §9.1）+ 防幻觉净化。

权威字段（scope/content_ids/source_refs/report_id/analysis_version）由服务端以真实数据
构造并覆盖，模型只负责"分析字段"。净化只对模型产出做结构清洗与内容 ID 求交，
防止把模型编造的 content 引用写进报告。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

MIN_TREND_SCOPE = 3          # 少于该样本量，趋势方向强制 uncertain
MAX_VIRAL_PATTERNS = 20      # 爆点模式条数上限（防模型灌水）
_MAX_STR_LEN = 2000
AnalysisVersion = "1.0"

_DIRECTIONS = ("rising", "stable", "falling", "uncertain")


class ReportScope(BaseModel):
    platforms: list[str] = Field(default_factory=list)
    content_ids: list[str] = Field(default_factory=list)
    time_range: dict[str, Any] = Field(default_factory=dict)


class ViralPattern(BaseModel):
    pattern: str = ""
    evidence_content_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    counterexamples: list[str] = Field(default_factory=list)


class PresentationStyle(BaseModel):
    visual: list[str] = Field(default_factory=list)
    text: list[str] = Field(default_factory=list)
    video: list[str] = Field(default_factory=list)
    interaction: list[str] = Field(default_factory=list)


class AccountPersona(BaseModel):
    hypotheses: list[str] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)  # content_id 引用
    confidence: float = Field(default=0.0, ge=0, le=1)


class TrendDirection(BaseModel):
    topics: list[str] = Field(default_factory=list)
    direction: Literal["rising", "stable", "falling", "uncertain"] = "uncertain"
    window: str = ""


class ReportPayload(BaseModel):
    report_id: str
    scope: ReportScope
    executive_summary: str = ""
    viral_patterns: list[ViralPattern] = Field(default_factory=list)
    presentation_style: PresentationStyle = Field(default_factory=PresentationStyle)
    account_persona: AccountPersona = Field(default_factory=AccountPersona)
    trend_direction: TrendDirection = Field(default_factory=TrendDirection)
    limitations: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    analysis_version: str = AnalysisVersion


class ReportSchemaError(ValueError):
    """raw 结构完全不可用（非 dict），服务层转 503。"""


def _as_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    return []


def _as_str(value: Any) -> str:
    """只接受 str；模型给了别的类型（数字/对象）不强行串化，归空。"""
    return value[:_MAX_STR_LEN] if isinstance(value, str) else ""


def _strs(value: Any) -> list[str]:
    out: list[str] = []
    for item in _as_list(value):
        if isinstance(item, str) and item.strip():
            out.append(item.strip()[:_MAX_STR_LEN])
    return out


def _clamp_confidence(value: Any) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, f))


def _intersect_ids(value: Any, allowed: set[str]) -> list[str]:
    """保留合法 content_id 引用（保持出现顺序、去重），丢弃越界引用。"""
    seen: set[str] = set()
    out: list[str] = []
    for item in _as_list(value):
        if isinstance(item, str) and item in allowed and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _clean_style(raw: Any) -> PresentationStyle:
    if not isinstance(raw, dict):
        return PresentationStyle()
    return PresentationStyle(
        visual=_strs(raw.get("visual")),
        text=_strs(raw.get("text")),
        video=_strs(raw.get("video")),
        interaction=_strs(raw.get("interaction")),
    )


def _clean_patterns(raw: Any, allowed: set[str]) -> list[ViralPattern]:
    """逐条净化；证据引用与允许集全不相交（或根本没给证据）→ 整条丢弃。"""
    out: list[ViralPattern] = []
    for item in _as_list(raw)[:MAX_VIRAL_PATTERNS]:
        if not isinstance(item, dict):
            continue
        ev = _intersect_ids(item.get("evidence_content_ids"), allowed)
        if not ev:
            continue
        out.append(
            ViralPattern(
                pattern=_as_str(item.get("pattern")),
                evidence_content_ids=ev,
                confidence=_clamp_confidence(item.get("confidence")),
                counterexamples=_intersect_ids(item.get("counterexamples"), allowed),
            )
        )
    return out


def _clean_persona(raw: Any, allowed: set[str]) -> AccountPersona:
    if not isinstance(raw, dict):
        return AccountPersona()
    return AccountPersona(
        hypotheses=_strs(raw.get("hypotheses")),
        evidence=_intersect_ids(raw.get("evidence"), allowed),
        confidence=_clamp_confidence(raw.get("confidence")),
    )


def _clean_trend(raw: Any, scope_size: int) -> TrendDirection:
    """非法 direction→uncertain；scope 样本不足→强制 uncertain 并在调用方写 limitations。"""
    if isinstance(raw, dict):
        direction = raw.get("direction", "uncertain")
        if direction not in _DIRECTIONS:
            direction = "uncertain"
    else:
        direction = "uncertain"
    if scope_size < MIN_TREND_SCOPE:
        direction = "uncertain"
    return TrendDirection(
        topics=_strs(raw.get("topics")) if isinstance(raw, dict) else [],
        direction=direction,
        window=_as_str(raw.get("window")) if isinstance(raw, dict) else "",
    )


def sanitize_report_payload(
    raw: Any,
    allowed_ids: list[str],
    *,
    report_id: str,
    scope: ReportScope,
    source_refs: list[str],
) -> ReportPayload:
    """把模型输出的任意结构清洗成合法 ReportPayload（防幻觉净化）。

    权威字段由参数注入；模型产出被裁剪、clamp、与 allowed_ids 求交。
    结构完全不可用（非 dict）时抛 ReportSchemaError。
    """
    if not isinstance(raw, dict):
        raise ReportSchemaError(f"模型输出非对象: {type(raw).__name__}")

    allowed = set(allowed_ids)
    # 强制真实输入范围（不受模型影响）
    scope.content_ids = [cid for cid in allowed_ids if cid in allowed]

    viral = _clean_patterns(raw.get("viral_patterns"), allowed)
    persona = _clean_persona(raw.get("account_persona"), allowed)
    trend = _clean_trend(raw.get("trend_direction"), len(scope.content_ids))

    limitations = _strs(raw.get("limitations"))
    if len(scope.content_ids) < MIN_TREND_SCOPE:
        limitation = (
            f"数据不足：范围仅 {len(scope.content_ids)} 条内容，低于趋势判定所需 "
            f"{MIN_TREND_SCOPE} 条，trend_direction 已强制为 uncertain。"
        )
        limitations.append(limitation)

    return ReportPayload(
        report_id=report_id,
        scope=scope,
        executive_summary=_as_str(raw.get("executive_summary")),
        viral_patterns=viral,
        presentation_style=_clean_style(raw.get("presentation_style")),
        account_persona=persona,
        trend_direction=trend,
        limitations=limitations,
        source_refs=source_refs,
        analysis_version=AnalysisVersion,
    )
