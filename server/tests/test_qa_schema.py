"""Q&A 蒸馏净化测试：白名单求交、越界引用丢弃、半条知识丢弃、0 对存活即拒。"""
from __future__ import annotations

import pytest

from app.schemas.qa import QaSchemaError, sanitize_qa_payload

REFS = {"aid-1": "analysis", "cid-1": "content", "cid-2": "content"}


def _pair(**kw) -> dict:
    base = {
        "question": "为什么这种夸张演绎能爆？",
        "answer": "结论：情绪反差强。拆解：…… 适用条件：…… 失败风险：……",
        "dimensions": {"technique": "夸张演绎", "transfer": ["换场景"], "transfer_risk": ["过度"]},
        "evidence": [{"source_kind": "analysis", "ref": "aid-1", "excerpt": "互动数据"}],
        "tags": ["爆款拆解"],
        "confidence": 0.7,
    }
    base.update(kw)
    return base


def test_happy_path_preserves_all_fields():
    out = sanitize_qa_payload({"qa_pairs": [_pair()]}, title="T", allowed_refs=REFS)
    assert out.title == "T" and len(out.qa_pairs) == 1
    p = out.qa_pairs[0]
    assert p.question == "为什么这种夸张演绎能爆？"
    assert p.dimensions["technique"] == "夸张演绎"
    assert p.dimensions["transfer"] == ["换场景"]
    assert p.dimensions["transfer_risk"] == ["过度"]
    assert p.evidence[0].ref == "aid-1" and p.evidence[0].source_kind == "analysis"
    assert p.evidence[0].excerpt == "互动数据"
    assert p.tags == ["爆款拆解"] and p.confidence == 0.7
    assert out.dropped_refs == 0


def test_out_of_scope_ref_dropped_and_counted():
    raw = {"qa_pairs": [_pair(evidence=[
        {"source_kind": "content", "ref": "cid-1", "excerpt": "ok"},
        {"source_kind": "content", "ref": "编造的-id", "excerpt": "假"},
        {"source_kind": "content", "ref": "https://fake.url", "excerpt": "假"},
    ])]}
    out = sanitize_qa_payload(raw, allowed_refs=REFS)
    p = out.qa_pairs[0]
    assert [e.ref for e in p.evidence] == ["cid-1"]
    assert out.dropped_refs == 2
    assert "编造的-id" in out.dropped_unknown_refs
    assert any("越界" in x for x in out.limitations)


def test_source_kind_is_authoritative_from_server():
    raw = {"qa_pairs": [_pair(evidence=[{"source_kind": "manual", "ref": "cid-1", "excerpt": "e"}])]}
    out = sanitize_qa_payload(raw, allowed_refs=REFS)
    assert out.qa_pairs[0].evidence[0].source_kind == "content"  # 服务端登记为准


def test_no_allowed_refs_means_all_evidence_dropped():
    raw = {"qa_pairs": [_pair(evidence=[{"source_kind": "analysis", "ref": "aid-1", "excerpt": "e"}])]}
    out = sanitize_qa_payload(raw, allowed_refs=None)
    assert out.qa_pairs[0].evidence == []
    assert out.dropped_refs == 1


@pytest.mark.parametrize("bad", [{"question": ""}, {"answer": "  "}, {"question": "  ", "answer": ""}])
def test_half_pair_is_dropped(bad):
    out = sanitize_qa_payload({"qa_pairs": [_pair(), _pair(**bad)]}, allowed_refs=REFS)
    assert len(out.qa_pairs) == 1  # 脏的那条不拖垮整批


def test_all_pairs_dirty_raises():
    raw = {"qa_pairs": [_pair(question=""), _pair(answer=""), "不是对象", 42]}
    with pytest.raises(QaSchemaError):
        sanitize_qa_payload(raw, allowed_refs=REFS)


def test_non_dict_raw_raises():
    for bad in (None, [], "字符串", 3):
        with pytest.raises(QaSchemaError):
            sanitize_qa_payload(bad, allowed_refs=REFS)


def test_missing_qa_pairs_raises():
    with pytest.raises(QaSchemaError):
        sanitize_qa_payload({"limitations": []}, allowed_refs=REFS)


def test_max_pairs_truncates():
    raw = {"qa_pairs": [_pair(question=f"问题{i}？") for i in range(10)]}
    out = sanitize_qa_payload(raw, allowed_refs=REFS, max_pairs=3)
    assert [p.question for p in out.qa_pairs] == ["问题0？", "问题1？", "问题2？"]
    with pytest.raises(QaSchemaError):  # 上限为 0 → 一对都不产 → 拒入库
        sanitize_qa_payload(raw, allowed_refs=REFS, max_pairs=0)


def test_unknown_dimension_moves_to_extra():
    raw = {"qa_pairs": [_pair(dimensions={
        "technique": "夸张", "bogus_dim": "未知", "extra": {"hand_note": "手工标注"},
    })]}
    dims = sanitize_qa_payload(raw, allowed_refs=REFS).qa_pairs[0].dimensions
    assert dims["technique"] == "夸张"
    assert dims["extra"] == {"bogus_dim": "未知", "hand_note": "手工标注"}
    assert "bogus_dim" not in dims


def test_dimension_type_mismatch_degrades_to_empty():
    raw = {"qa_pairs": [_pair(dimensions={
        "technique": 123, "persona": None, "transfer": "不是数组", "transfer_risk": [1, "ok", ""],
    })]}
    dims = sanitize_qa_payload(raw, allowed_refs=REFS).qa_pairs[0].dimensions
    assert "technique" not in dims and "persona" not in dims
    assert "transfer" not in dims
    assert dims["transfer_risk"] == ["ok"]


def test_dimensions_not_dict_is_empty():
    raw = {"qa_pairs": [_pair(dimensions="乱码")]}
    assert sanitize_qa_payload(raw, allowed_refs=REFS).qa_pairs[0].dimensions == {}


@pytest.mark.parametrize("value,expect", [(2, 1.0), (-1, 0.0), ("0.4", 0.4), (True, 0.0), ("abc", 0.0), (None, 0.0)])
def test_confidence_clamped(value, expect):
    out = sanitize_qa_payload({"qa_pairs": [_pair(confidence=value)]}, allowed_refs=REFS)
    assert out.qa_pairs[0].confidence == expect


def test_evidence_not_list_is_empty():
    raw = {"qa_pairs": [_pair(evidence={"ref": "aid-1"})]}
    out = sanitize_qa_payload(raw, allowed_refs=REFS)
    assert out.qa_pairs[0].evidence == [] and out.dropped_refs == 0


def test_tags_cleaned_and_bounded():
    raw = {"qa_pairs": [_pair(tags=["a", "", "  ", 5, "b"] + [f"t{i}" for i in range(20)])]}
    tags = sanitize_qa_payload(raw, allowed_refs=REFS).qa_pairs[0].tags
    assert tags[:2] == ["a", "b"] and len(tags) == 8


def test_limitations_carried_and_bounded():
    raw = {"qa_pairs": [_pair()], "limitations": ["素材有限"] + [f"l{i}" for i in range(20)]}
    out = sanitize_qa_payload(raw, allowed_refs=REFS)
    assert "素材有限" in out.limitations and len(out.limitations) <= 11
    assert out.schema_version == "1.0"
