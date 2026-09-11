"""SqliteReviewRepository / SqliteContentRepository(审核相关) roundtrip 测试。"""
from __future__ import annotations

import pytest

from app.domain.enums import ClaimStatus, ClaimType, ContentType, EvidenceKind, ReviewStatus
from app.domain.models import Claim, Content, Evidence, Report, ReviewDecision
from app.repositories.sqlite import SqliteContentRepository, SqliteReviewRepository


def make_content(content_id: str, text: str | None = None, title: str | None = None) -> Content:
    return Content(
        content_id=content_id,
        platform="xhs",
        platform_item_id=content_id.split(":", 1)[1],
        content_type=ContentType.NOTE,
        title=title or f"标题 {content_id}",
        text=text or f"正文 {content_id}",
    )


@pytest.fixture
def content_repo():
    return SqliteContentRepository()


@pytest.fixture
def review_repo():
    return SqliteReviewRepository()


@pytest.fixture
async def seeded_content(init_test_db, content_repo):
    content = make_content("xhs:seed001", text="咖啡对提神有帮助，但也可能造成心悸。")
    await content_repo.upsert(content)
    return content


async def test_claim_roundtrip(init_test_db, seeded_content, review_repo):
    claim = Claim(
        claim_id="claim-1", content_id="xhs:seed001", text="咖啡可能造成心悸",
        status=ClaimStatus.UNVERIFIED, confidence=0.9, claim_type=ClaimType.FACT,
        meta={"entities": ["咖啡"]},
    )
    await review_repo.add_claim(claim)
    got = await review_repo.get_claim("claim-1")
    assert got is not None
    assert got.text == claim.text
    assert got.claim_type == ClaimType.FACT
    assert got.status == ClaimStatus.UNVERIFIED
    assert got.meta == {"entities": ["咖啡"]}

    # 更新
    claim.status = ClaimStatus.SUPPORTED
    claim.confidence = 0.95
    await review_repo.update_claim(claim)
    got = await review_repo.get_claim("claim-1")
    assert got.status == ClaimStatus.SUPPORTED
    assert got.confidence == pytest.approx(0.95)

    # list_claims
    listed = await review_repo.list_claims("xhs:seed001")
    assert [c.claim_id for c in listed] == ["claim-1"]


async def test_evidence_and_decision_roundtrip(init_test_db, seeded_content, review_repo):
    claim = Claim(claim_id="claim-2", content_id="xhs:seed001", text="某断言")
    await review_repo.add_claim(claim)

    ev = Evidence(
        evidence_id="ev-1", claim_id="claim-2", source_kind=EvidenceKind.CONTENT,
        source_ref="xhs:seed001", excerpt="咖啡可能造成心悸", supports=True, strength=0.8,
    )
    await review_repo.add_evidence(ev)
    got_ev = await review_repo.list_evidence("claim-2")
    assert len(got_ev) == 1
    assert got_ev[0].source_kind == EvidenceKind.CONTENT
    assert got_ev[0].source_ref == "xhs:seed001"
    assert got_ev[0].supports is True

    dec = ReviewDecision(
        decision_id="dec-1", claim_id="claim-2", status=ClaimStatus.SUPPORTED,
        rationale="本地库有佐证", reviewer="rule-engine", evidence_ids=["ev-1"],
    )
    await review_repo.add_decision(dec)
    got_dec = await review_repo.list_decisions("claim-2")
    assert len(got_dec) == 1
    assert got_dec[0].status == ClaimStatus.SUPPORTED
    assert got_dec[0].evidence_ids == ["ev-1"]


async def test_report_roundtrip_and_order(init_test_db, review_repo):
    r1 = Report(report_id="rep-1", title="报告一", content_ids=["xhs:a"], payload={"k": 1})
    r2 = Report(report_id="rep-2", title="报告二", content_ids=["xhs:b"], payload={"k": 2}, markdown="# 报告二")
    await review_repo.add_report(r1)
    await review_repo.add_report(r2)

    got = await review_repo.get_report("rep-1")
    assert got is not None
    assert got.title == "报告一"
    assert got.payload == {"k": 1}
    assert got.schema_version == "1.0"

    # list_reports：后插在前（rowid DESC）
    listed = await review_repo.list_reports()
    assert [r.report_id for r in listed] == ["rep-2", "rep-1"]
    listed_paged = await review_repo.list_reports(limit=1, offset=1)
    assert [r.report_id for r in listed_paged] == ["rep-1"]


async def test_save_review_batch_atomic(init_test_db, seeded_content, content_repo, review_repo):
    claims = [
        Claim(claim_id="c1", content_id="xhs:seed001", text="咖啡可能造成心悸",
              status=ClaimStatus.SUPPORTED, confidence=0.85, claim_type=ClaimType.FACT),
        Claim(claim_id="c2", content_id="xhs:seed001", text="咖啡可提神",
              status=ClaimStatus.SUPPORTED, confidence=0.8),
    ]
    evidences = [
        Evidence(evidence_id="e1", claim_id="c1", source_kind=EvidenceKind.CONTENT,
                 source_ref="xhs:seed001", excerpt="咖啡可能造成心悸", supports=True),
        Evidence(evidence_id="e2", claim_id="c2", source_kind=EvidenceKind.CONTENT,
                 source_ref="xhs:seed001", excerpt="咖啡对提神有帮助", supports=True),
    ]
    decisions = [
        ReviewDecision(decision_id="d1", claim_id="c1", status=ClaimStatus.SUPPORTED,
                       rationale="本地佐证", evidence_ids=["e1"]),
        ReviewDecision(decision_id="d2", claim_id="c2", status=ClaimStatus.SUPPORTED,
                       rationale="本地佐证", evidence_ids=["e2"]),
    ]
    await review_repo.save_review_batch(
        "xhs:seed001", claims, evidences, decisions, ReviewStatus.APPROVED
    )

    # content 终态已推进
    content = await content_repo.get("xhs:seed001")
    assert content.review_status == ReviewStatus.APPROVED

    # claims / evidence / decisions 全部可见
    assert len(await review_repo.list_claims("xhs:seed001")) == 2
    assert len(await review_repo.list_evidence("c1")) == 1
    got_dec = await review_repo.list_decisions("c1")
    assert got_dec[0].evidence_ids == ["e1"]


async def test_save_review_batch_second_call_adds_batch_keeps_old(
    init_test_db, seeded_content, content_repo, review_repo
):
    """二次 save_review_batch 默认**新增批次**：旧批次原样保留，生效批次切到新的一批。"""
    first = [
        Claim(claim_id="c1", content_id="xhs:seed001", text="旧断言", status=ClaimStatus.UNVERIFIED)
    ]
    b1 = await review_repo.save_review_batch(
        "xhs:seed001", first, [], [], ReviewStatus.EXTRACTED
    )
    content = await content_repo.get("xhs:seed001")
    assert content.review_status == ReviewStatus.EXTRACTED
    assert content.active_batch_id == b1

    second = [
        Claim(claim_id="c2", content_id="xhs:seed001", text="新断言", status=ClaimStatus.UNVERIFIED)
    ]
    b2 = await review_repo.save_review_batch(
        "xhs:seed001", second, [], [], ReviewStatus.EXTRACTED
    )
    assert b2 != b1

    # 默认读生效批次（新的一批）；旧的按 batch_id 仍完整可取
    assert [c.claim_id for c in await review_repo.list_claims("xhs:seed001")] == ["c2"]
    assert [c.claim_id for c in await review_repo.list_claims("xhs:seed001", b1)] == ["c1"]
    assert (await content_repo.get("xhs:seed001")).active_batch_id == b2
    assert (await review_repo.get_claim("c1")).text == "旧断言"  # 没被清、也没被改写


async def test_update_review_status_and_unknown_id(init_test_db, seeded_content, content_repo):
    ok = await content_repo.update_review_status("xhs:seed001", ReviewStatus.EXTRACTED)
    assert ok is True
    assert (await content_repo.get("xhs:seed001")).review_status == ReviewStatus.EXTRACTED

    missing = await content_repo.update_review_status("xhs:nope", ReviewStatus.APPROVED)
    assert missing is False


async def test_search_keyword_with_escape_and_exclude(init_test_db, content_repo):
    await content_repo.upsert(make_content("xhs:a", text="咖啡提神 100% 有效"))
    await content_repo.upsert(make_content("xhs:b", text="绿茶含咖啡因，促代谢"))
    await content_repo.upsert(make_content("xhs:c", text="牛奶补钙"))

    hits = await content_repo.search("咖啡因")
    assert {c.content_id for c in hits} == {"xhs:b"}

    # 空格分词 = AND? 不：OR。query 已分词，OR 命中任一 term
    hits2 = await content_repo.search("咖啡 牛奶")
    assert {c.content_id for c in hits2} == {"xhs:a", "xhs:b", "xhs:c"}

    # exclude 排除自身
    hits3 = await content_repo.search("咖啡 牛奶", exclude_content_id="xhs:a")
    assert "xhs:a" not in {c.content_id for c in hits3}

    # limit
    hits4 = await content_repo.search("咖啡 牛奶 提神 绿茶 补钙", limit=2)
    assert len(hits4) == 2

    # 空 query
    assert await content_repo.search("   ") == []

    # 通配符转义：% 不命中所有
    hits5 = await content_repo.search("100%")
    assert {c.content_id for c in hits5} == {"xhs:a"}
