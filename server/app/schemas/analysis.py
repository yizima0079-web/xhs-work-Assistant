"""作品分析 payload schema（单条深度 + 可选同话题横向对比）+ 防幻觉净化。

权威字段（analysis_id/content_id/schema_version/comparison.baseline_count）由服务端注入或
以真实数据推导；模型只负责分析字段。净化要点：
- verdict 白名单（viral|flat|uncertain），非法降级 uncertain；
- comparison.baseline_content_ids 必须与真实检索出的同话题候选求交，越界引用丢弃；
- baseline_count 以净化后的真实基线数为准（服务端权威，不信模型数字）；
- 无有效基线时 differentiators/shared_patterns 清空，防止挂空对比；
- 结构完全不可用（非 dict）抛 AnalysisSchemaError，服务层转 503 不落库。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

AnalysisVersion = "1.0"
MAX_REASONS = 8
_MAX_STR_LEN = 1000

_VERDICTS = ("viral", "flat", "uncertain")


class AnalysisSchemaError(ValueError):
    """raw 结构完全不可用（非 dict），服务层转 503。"""


class AnalysisReason(BaseModel):
    factor: str = ""      # 归因因子（如"开头强钩子"）
    evidence: str = ""    # 数据/事实依据（须来自给定内容数据，禁止编造）
    confidence: float = Field(default=0.0, ge=0, le=1)


class AnalysisComparison(BaseModel):
    baseline_content_ids: list[str] = Field(default_factory=list)
    baseline_count: int = 0
    differentiators: list[str] = Field(default_factory=list)
    shared_patterns: list[str] = Field(default_factory=list)


class AnalysisPayload(BaseModel):
    analysis_id: str
    content_id: str
    verdict: Literal["viral", "flat", "uncertain"] = "uncertain"
    summary: str = ""
    topic: str = ""
    viral_reasons: list[AnalysisReason] = Field(default_factory=list)
    flat_reasons: list[AnalysisReason] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)
    audience: list[str] = Field(default_factory=list)
    comparison: AnalysisComparison = Field(default_factory=AnalysisComparison)
    suggestions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    limitations: list[str] = Field(default_factory=list)
    schema_version: str = AnalysisVersion


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _as_str(value: Any) -> str:
    return value[:_MAX_STR_LEN] if isinstance(value, str) else ""


def _strs(value: Any) -> list[str]:
    out: list[str] = []
    for item in _as_list(value):
        if isinstance(item, str) and item.strip():
            out.append(item.strip()[:_MAX_STR_LEN])
    return out


def _clamp(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, f))


def _intersect(value: Any, allowed: set[str]) -> list[str]:
    """保留合法 content_id（保持顺序、去重），越界丢弃。"""
    seen: set[str] = set()
    out: list[str] = []
    for item in _as_list(value):
        if isinstance(item, str) and item in allowed and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _reasons(value: Any) -> list[AnalysisReason]:
    """逐条净化归因；factor/evidence 全空则该条丢弃，confidence clamp。"""
    out: list[AnalysisReason] = []
    for item in _as_list(value)[:MAX_REASONS]:
        if not isinstance(item, dict):
            continue
        factor = _as_str(item.get("factor"))
        evidence = _as_str(item.get("evidence"))
        if not factor and not evidence:
            continue
        out.append(
            AnalysisReason(
                factor=factor,
                evidence=evidence,
                confidence=_clamp(item.get("confidence")),
            )
        )
    return out


def sanitize_analysis_payload(
    raw: Any,
    *,
    analysis_id: str,
    content_id: str,
    allowed_baselines: list[str],
) -> AnalysisPayload:
    """把模型输出的任意结构清洗成合法 AnalysisPayload（防幻觉净化）。"""
    if not isinstance(raw, dict):
        raise AnalysisSchemaError(f"模型输出非对象: {type(raw).__name__}")

    verdict_raw = raw.get("verdict")
    verdict = (
        verdict_raw if isinstance(verdict_raw, str) and verdict_raw in _VERDICTS else "uncertain"
    )

    cmp = raw.get("comparison")
    cmp_raw = cmp if isinstance(cmp, dict) else {}
    allowed = set(allowed_baselines)
    baseline_ids = _intersect(cmp_raw.get("baseline_content_ids"), allowed)

    limitations = _strs(raw.get("limitations"))

    # 无有效基线 → 挂空对比整体清空，避免"看似对比实为杜撰"
    has_baseline = bool(baseline_ids)
    differentiators = _strs(cmp_raw.get("differentiators")) if has_baseline else []
    shared_patterns = _strs(cmp_raw.get("shared_patterns")) if has_baseline else []

    if not allowed_baselines:
        note = "无同话题候选可对比，横向对比为空，结论基于单条深度分析。"
        if note not in limitations:
            limitations.append(note)
    elif not has_baseline and (differentiators or shared_patterns or cmp_raw):
        # 模型声称做了对比但没引用任何真实基线 → 说明不落地
        note = "模型未引用有效同话题基线，横向对比字段已清空。"
        if note not in limitations:
            limitations.append(note)

    return AnalysisPayload(
        analysis_id=analysis_id,
        content_id=content_id,
        verdict=verdict,  # type: ignore[arg-type]
        summary=_as_str(raw.get("summary")),
        topic=_as_str(raw.get("topic")),
        viral_reasons=_reasons(raw.get("viral_reasons")),
        flat_reasons=_reasons(raw.get("flat_reasons")),
        hooks=_strs(raw.get("hooks")),
        audience=_strs(raw.get("audience")),
        comparison=AnalysisComparison(
            baseline_content_ids=baseline_ids,
            baseline_count=len(baseline_ids),  # 服务端权威：真实基线条数
            differentiators=differentiators,
            shared_patterns=shared_patterns,
        ),
        suggestions=_strs(raw.get("suggestions")),
        confidence=_clamp(raw.get("confidence")),
        limitations=limitations,
        schema_version=AnalysisVersion,
    )
