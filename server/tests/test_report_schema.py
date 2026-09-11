"""报告 payload schema + 防幻觉净化边界测试。"""
from __future__ import annotations

import pytest

from app.schemas.report import (
    ReportPayload,
    ReportScope,
    ReportSchemaError,
    sanitize_report_payload,
)


def build_scope(cids: list[str]) -> ReportScope:
    return ReportScope(platforms=["xhs"], content_ids=cids, time_range={})


def sanitize(raw, allowed, cids=None, refs=None):
    return sanitize_report_payload(
        raw, allowed, report_id="rep-1",
        scope=build_scope(cids if cids is not None else allowed),
        source_refs=refs if refs is not None else ["https://example.com/1"],
    )


def test_sanitize_keeps_legit_refs():
    raw = {
        "executive_summary": "咖啡话题有爆点",
        "viral_patterns": [
            {
                "pattern": "提神诉求为主",
                "evidence_content_ids": ["xhs:a", "xhs:b"],
                "confidence": 0.9,
                "counterexamples": ["xhs:c"],
            }
        ],
        "account_persona": {"hypotheses": ["养生博主"], "evidence": ["xhs:a"], "confidence": 0.7},
        "trend_direction": {"topics": ["咖啡"], "direction": "rising", "window": "近7天"},
        "limitations": ["仅本地样本"],
    }
    payload = sanitize(raw, ["xhs:a", "xhs:b", "xhs:c"])
    assert payload.executive_summary == "咖啡话题有爆点"
    assert payload.viral_patterns[0].evidence_content_ids == ["xhs:a", "xhs:b"]
    assert payload.viral_patterns[0].counterexamples == ["xhs:c"]
    assert payload.account_persona.evidence == ["xhs:a"]
    assert payload.trend_direction.direction == "rising"
    assert payload.scope.content_ids == ["xhs:a", "xhs:b", "xhs:c"]
    assert payload.source_refs == ["https://example.com/1"]


def test_sanitize_drops_out_of_scope_refs_and_whole_patterns():
    raw = {
        "viral_patterns": [
            {"pattern": "编造引用", "evidence_content_ids": ["xhs:ghost"], "confidence": 0.5},
            {"pattern": "混合引用", "evidence_content_ids": ["xhs:ok", "xhs:ghost"], "confidence": 0.5},
        ]
    }
    payload = sanitize(raw, ["xhs:ok"])
    # 全部越界 → 删整条；部分越界 → 只留合法
    assert [p.pattern for p in payload.viral_patterns] == ["混合引用"]
    assert payload.viral_patterns[0].evidence_content_ids == ["xhs:ok"]


def test_sanitize_clamps_confidence_and_wrong_types():
    raw = {
        "executive_summary": 123,                      # 非 str → 空串
        "viral_patterns": [
            {"pattern": "p", "evidence_content_ids": ["xhs:a"], "confidence": "high"},
        ],
        "presentation_style": "not-a-dict",
        "account_persona": {"confidence": 99, "evidence": ["xhs:a", "xhs:b"]},
    }
    payload = sanitize(raw, ["xhs:a", "xhs:b", "xhs:c"])
    assert payload.executive_summary == ""
    assert payload.viral_patterns[0].confidence == 0.0       # "high" 解析失败 → 0
    assert payload.viral_patterns[0].pattern == "p"
    assert payload.presentation_style.visual == []
    assert payload.account_persona.confidence == 1.0          # clamp
    assert payload.account_persona.evidence == ["xhs:a", "xhs:b"]


def test_sanitize_small_scope_forces_uncertain():
    raw = {"trend_direction": {"direction": "rising", "window": "7d", "topics": ["t"]}}
    payload = sanitize(raw, ["xhs:a", "xhs:b"])  # < MIN_TREND_SCOPE(3)
    assert payload.trend_direction.direction == "uncertain"
    assert any("uncertain" in lim for lim in payload.limitations)


def test_sanitize_invalid_direction_falls_back():
    raw = {"trend_direction": {"direction": "moonwalk", "topics": ["t"]}}
    payload = sanitize(raw, ["xhs:a", "xhs:b", "xhs:c", "xhs:d"])
    assert payload.trend_direction.direction == "uncertain"


def test_sanitize_non_dict_raises():
    with pytest.raises(ReportSchemaError):
        sanitize(["not", "dict"], ["xhs:a"])


def test_sanitize_null_fields_tolerated():
    payload = sanitize(None if False else {"viral_patterns": None}, ["xhs:a"], cids=["xhs:a"])
    assert payload.viral_patterns == []
    assert payload.executive_summary == ""
    assert isinstance(payload, ReportPayload)
