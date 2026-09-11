"""作品分析 payload schema + 防幻觉净化边界测试。"""
from __future__ import annotations

import pytest

from app.schemas.analysis import (
    AnalysisPayload,
    AnalysisSchemaError,
    sanitize_analysis_payload,
)


def sanitize(raw, allowed_baselines):
    return sanitize_analysis_payload(
        raw,
        analysis_id="an-1",
        content_id="xhs:a",
        allowed_baselines=allowed_baselines,
    )


def test_sanitize_keeps_legit_fields():
    raw = {
        "verdict": "viral",
        "summary": "强钩子+反差选题拉动互动",
        "topic": "AI 眼镜测评",
        "viral_reasons": [
            {"factor": "开头强钩子", "evidence": "前 3 秒抛自费测评悬念", "confidence": 0.9}
        ],
        "flat_reasons": [],
        "hooks": ["自费 2000 块实测"],
        "audience": ["数码爱好者"],
        "comparison": {
            "baseline_content_ids": ["xhs:b", "xhs:c"],
            "baseline_count": 99,  # 服务端权威重算，不信模型数字
            "differentiators": ["同话题多为云评测，本条真机实测"],
            "shared_patterns": ["都在标题点 AI 眼镜"],
        },
        "suggestions": ["突出真机实测成本"],
        "confidence": 0.85,
        "limitations": ["样本有限"],
    }
    payload = sanitize(raw, ["xhs:b", "xhs:c"])
    assert payload.verdict == "viral"
    assert payload.summary == "强钩子+反差选题拉动互动"
    assert payload.viral_reasons[0].factor == "开头强钩子"
    assert payload.viral_reasons[0].confidence == pytest.approx(0.9)
    assert payload.comparison.baseline_content_ids == ["xhs:b", "xhs:c"]
    assert payload.comparison.baseline_count == 2          # 服务端权威，覆盖模型的 99
    assert payload.comparison.differentiators == ["同话题多为云评测，本条真机实测"]
    assert payload.confidence == pytest.approx(0.85)
    assert payload.analysis_id == "an-1"
    assert payload.content_id == "xhs:a"
    assert payload.schema_version == "1.0"


def test_sanitize_drops_fabricated_baseline_ids():
    raw = {
        "verdict": "flat",
        "comparison": {
            "baseline_content_ids": ["xhs:b", "xhs:ghost"],
            "differentiators": ["正文更像软文"],
            "shared_patterns": [],
        },
    }
    payload = sanitize(raw, ["xhs:b", "xhs:c"])
    assert payload.comparison.baseline_content_ids == ["xhs:b"]
    assert payload.comparison.baseline_count == 1
    assert payload.comparison.differentiators == ["正文更像软文"]  # 有真实基线，差异点保留


def test_sanitize_clears_orphan_comparison_without_baseline():
    # 模型给了差异点但没引用任何真实基线 → 挂空对比整段清空 + 局限说明
    raw = {
        "verdict": "uncertain",
        "comparison": {
            "differentiators": ["看似对比但其实没基线"],
            "shared_patterns": [],
        },
    }
    payload = sanitize(raw, ["xhs:b"])  # 候选存在但模型一个都没引用
    assert payload.comparison.baseline_content_ids == []
    assert payload.comparison.baseline_count == 0
    assert payload.comparison.differentiators == []
    assert any("清空" in lim for lim in payload.limitations)

    # 候选本身为空 → 不同 note
    raw2 = {"verdict": "viral"}
    payload2 = sanitize(raw2, [])
    assert any("无同话题候选" in lim for lim in payload2.limitations)


def test_sanitize_invalid_verdict_falls_back_uncertain():
    payload = sanitize({"verdict": "superstar", "confidence": 2}, ["xhs:b"])
    assert payload.verdict == "uncertain"
    assert payload.confidence == 1.0          # clamp
    payload2 = sanitize({"verdict": None}, ["xhs:b"])
    assert payload2.verdict == "uncertain"


def test_sanitize_cleans_wrong_types():
    raw = {
        "verdict": "viral",
        "summary": 123,
        "viral_reasons": [
            {"factor": "f", "evidence": "e", "confidence": "high"},      # conf 解析失败 → 0
            {"factor": "", "evidence": "", "confidence": 0.9},          # 全空 → 丢弃
            "not-a-dict",                                                # 非 dict → 跳过
        ],
        "hooks": ["ok", 42, None],
    }
    payload = sanitize(raw, ["xhs:b"])
    assert payload.summary == ""
    assert len(payload.viral_reasons) == 1
    assert payload.viral_reasons[0].confidence == 0.0
    assert payload.hooks == ["ok"]


def test_sanitize_non_dict_raises():
    with pytest.raises(AnalysisSchemaError):
        sanitize(["not", "dict"], ["xhs:b"])


def test_sanitize_null_tolerated_and_default_payload_valid():
    payload = sanitize({"verdict": None}, [])
    assert isinstance(payload, AnalysisPayload)
    assert payload.verdict == "uncertain"
    assert payload.viral_reasons == []
    assert payload.comparison.baseline_content_ids == []
