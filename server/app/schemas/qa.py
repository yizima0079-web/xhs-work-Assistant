"""Q&A 蒸馏 payload schema + 防幻觉净化。

知识原子是「问题 + 答案 + 拆解维度 + 证据 + 溯源」，不是 markdown 切片。净化要点：
- question/answer 任一为空 → 该对整条丢弃（半条知识没有意义）；
- evidence.ref 必须与**服务端给定的可引用来源**求交，越界引用丢弃并计数（防编造出处）；
- source_kind 以服务端登记的种类为准，模型自报不一致时改回服务端值；
- dimensions 只认六个预设维度，未知键统一收进 `extra`（不丢信息也不污染检索字段）；
- confidence clamp 到 [0,1]；单对脏数据不拖垮整批；
- **清洗后 0 对存活 → 抛 QaSchemaError**，服务层转 ModelParseError，绝不落空文档进库。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.domain.models import KbDocument, KbQaPair  # 单向依赖：domain 不反向 import schemas

QaVersion = "1.0"
MAX_PAIRS = 8

_QUESTION_MAX = 500
_ANSWER_MAX = 4000
_DIM_MAX = 2000
_EXCERPT_MAX = 1000
_MAX_LIMITATIONS = 10

_DIM_TEXT_KEYS = ("technique", "persona", "hook", "structure")
_DIM_LIST_KEYS = ("transfer", "transfer_risk")
SourceKind = Literal["analysis", "content", "manual"]


class QaSchemaError(ValueError):
    """raw 结构不可用或清洗后无有效问答对，服务层转 ModelParseError。"""


class QaEvidence(BaseModel):
    source_kind: SourceKind = "content"
    ref: str = ""       # 只能是服务端给定的来源 id（analysis_id / content_id）
    excerpt: str = ""   # 摘自素材原文，不得改写


class QaPairDraft(BaseModel):
    question: str
    answer: str
    dimensions: dict[str, Any] = Field(default_factory=dict)
    evidence: list[QaEvidence] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)


class QaDistillPayload(BaseModel):
    title: str = ""
    qa_pairs: list[QaPairDraft]
    limitations: list[str] = Field(default_factory=list)
    dropped_refs: int = 0                      # 越界引用被丢弃的条数（审核可见）
    dropped_unknown_refs: list[str] = Field(default_factory=list)
    schema_version: str = QaVersion


class DistillResult(BaseModel):
    """一次蒸馏（或手动录入）的产物：容器文档 + 草稿问答对。"""
    doc: KbDocument
    pairs: list[KbQaPair] = Field(default_factory=list)
    reused: bool = False            # 同来源已存在 Q&A 文档且未 force → 直接复用
    replaced_drafts: int = 0        # force 重蒸馏时被替换掉的旧草稿数（approved 永不自动删除）
    limitations: list[str] = Field(default_factory=list)
    dropped_refs: int = 0


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


def _as_str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _strs(value: Any, limit: int = _DIM_MAX) -> list[str]:
    out: list[str] = []
    for item in _as_list(value):
        if isinstance(item, str) and item.strip():
            out.append(item.strip()[:limit])
    return out


def _clamp(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, f))


def _dimensions(value: Any) -> dict[str, Any]:
    """六个预设维度白名单；未知键收进 extra，非 dict 一律当空。"""
    raw = value if isinstance(value, dict) else {}
    out: dict[str, Any] = {}
    for key in _DIM_TEXT_KEYS:
        text = _as_str(raw.get(key)).strip()[: _DIM_MAX]
        if text:
            out[key] = text
    for key in _DIM_LIST_KEYS:
        items = _strs(raw.get(key))
        if items:
            out[key] = items
    known = set(_DIM_TEXT_KEYS) | set(_DIM_LIST_KEYS)
    extra = {k: v for k, v in raw.items() if k not in known and k != "extra"}
    if isinstance(raw.get("extra"), dict):
        extra.update(raw["extra"])
    if extra:
        out["extra"] = extra
    return out


def _evidence(value: Any, allowed: dict[str, str]) -> tuple[list[QaEvidence], int, list[str]]:
    """逐条净化证据：ref 不在允许集 → 丢弃并计数；source_kind 以服务端登记为准。"""
    out: list[QaEvidence] = []
    dropped = 0
    unknown: list[str] = []
    for item in _as_list(value):
        if not isinstance(item, dict):
            continue
        ref = _as_str(item.get("ref")).strip()
        if ref not in allowed:
            dropped += 1
            if ref:
                unknown.append(ref)
            continue
        excerpt = _as_str(item.get("excerpt")).strip()[:_EXCERPT_MAX]
        out.append(QaEvidence(source_kind=allowed[ref], ref=ref, excerpt=excerpt))
    return out, dropped, unknown


def sanitize_qa_payload(
    raw: Any,
    *,
    title: str = "",
    allowed_refs: dict[str, str] | None = None,
    max_pairs: int = MAX_PAIRS,
) -> QaDistillPayload:
    """把模型输出的任意结构清洗成合法 QaDistillPayload。

    allowed_refs: {ref: source_kind}，服务端权威的可引用来源表；越界 ref 全部丢弃。
    """
    if not isinstance(raw, dict):
        raise QaSchemaError(f"模型输出非对象: {type(raw).__name__}")
    raw_pairs = raw.get("qa_pairs")
    if not isinstance(raw_pairs, list):
        raise QaSchemaError("模型输出缺少 qa_pairs 数组")

    allowed = dict(allowed_refs or {})
    pairs: list[QaPairDraft] = []
    dropped_refs = 0
    unknown_refs: list[str] = []
    for item in raw_pairs[: max(int(max_pairs), 0)]:
        if not isinstance(item, dict):
            continue
        question = _as_str(item.get("question")).strip()[:_QUESTION_MAX]
        answer = _as_str(item.get("answer")).strip()[:_ANSWER_MAX]
        if not question or not answer:
            continue  # 半条知识不收：问题或答案缺失即整对丢弃
        evidence, dropped, unknown = _evidence(item.get("evidence"), allowed)
        dropped_refs += dropped
        unknown_refs.extend(unknown)
        pairs.append(
            QaPairDraft(
                question=question,
                answer=answer,
                dimensions=_dimensions(item.get("dimensions")),
                evidence=evidence,
                tags=_strs(item.get("tags"), 64)[:8],
                confidence=_clamp(item.get("confidence")),
            )
        )

    if not pairs:
        raise QaSchemaError(
            f"未产出有效问答对（候选 {len(raw_pairs)} 条，问题/答案为空即被丢弃）"
        )

    limitations = _strs(raw.get("limitations"))[:_MAX_LIMITATIONS]
    if dropped_refs:
        limitations.append(f"已丢弃 {dropped_refs} 条越界引用的证据（不在允许来源内）")
    return QaDistillPayload(
        title=title,
        qa_pairs=pairs,
        limitations=limitations,
        dropped_refs=dropped_refs,
        dropped_unknown_refs=sorted(set(unknown_refs))[:10],
    )
