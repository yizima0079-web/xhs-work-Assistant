"""重新审核（force 新增批次，旧批次保留）与「按内容查报告」的离线验证。"""
from __future__ import annotations

import pytest

from app.domain.enums import ClaimStatus, ClaimType, EvidenceKind, ReviewStatus
from app.domain.models import Claim, Evidence, Report, ReviewDecision
from app.repositories.sqlite import SqliteContentRepository, SqliteReviewRepository
from tests.test_review_repository import make_content


@pytest.fixture
def content_repo():
    return SqliteContentRepository()


@pytest.fixture
def review_repo():
    return SqliteReviewRepository()


def _claim(cid: str, n: int, text: str = "") -> Claim:
    return Claim(
        claim_id=f"{cid}-c{n}", content_id=cid, text=text or f"断言 {n}",
        status=ClaimStatus.SUPPORTED, confidence=0.9, claim_type=ClaimType.FACT,
    )


def _evidence(cid: str, n: int) -> Evidence:
    return Evidence(
        evidence_id=f"{cid}-e{n}", claim_id=f"{cid}-c{n}", source_kind=EvidenceKind.CONTENT,
        source_ref=f"{cid}:other", excerpt=f"证据 {n}", supports=True, strength=0.8,
    )


def _decision(cid: str, n: int) -> ReviewDecision:
    return ReviewDecision(
        decision_id=f"{cid}-d{n}", claim_id=f"{cid}-c{n}",
        status=ClaimStatus.SUPPORTED, rationale="自动", reviewer="model",
    )


async def test_rereview_adds_batch_and_keeps_old(init_test_db, review_repo, content_repo):
    """重审新增批次：生效批次是新的一批，旧断言/证据/判定**一条不少**地留着。"""
    cid = "xhs:seed001"
    await content_repo.upsert(make_content(cid))

    await review_repo.save_review_batch(
        cid, [_claim(cid, 1), _claim(cid, 2)], [_evidence(cid, 1), _evidence(cid, 2)],
        [_decision(cid, 1), _decision(cid, 2)], ReviewStatus.APPROVED,
    )
    assert len(await review_repo.list_claims(cid)) == 2

    # 第二轮只产出 1 条
    await review_repo.save_review_batch(
        cid, [_claim(cid, 9)], [_evidence(cid, 9)], [_decision(cid, 9)],
        ReviewStatus.EXTRACTED,
    )
    # 默认读生效批次 → 只回新的一批，不是叠加成 3 条
    assert [c.claim_id for c in await review_repo.list_claims(cid)] == [f"{cid}-c9"]

    # 两批并存：序号新→旧，active 只落在最新那批
    batches = await review_repo.list_review_batches(cid)
    assert [b["batch_seq"] for b in batches] == [2, 1]
    assert batches[0]["active"] is True and batches[0]["claim_count"] == 1
    assert batches[1]["active"] is False and batches[1]["claim_count"] == 2
    assert [
        c.claim_id for c in await review_repo.list_claims(cid, batches[1]["batch_id"])
    ] == [f"{cid}-c1", f"{cid}-c2"]

    async with __import__("app.db", fromlist=["connect"]).connect() as db:
        cur = await db.execute("SELECT COUNT(*) FROM evidence")
        assert (await cur.fetchone())[0] == 3        # 1 + 2：旧证据一条没丢
        cur = await db.execute("SELECT COUNT(*) FROM review_decisions")
        assert (await cur.fetchone())[0] == 3        # 旧判定同理

    content = await content_repo.get(cid)
    assert content is not None and content.review_status == ReviewStatus.EXTRACTED


async def test_replace_same_batch_clears_only_that_batch(init_test_db, review_repo, content_repo):
    """replace=True + 显式 batch_id：同批次覆盖重试，不碰别的版本，序号也不变。"""
    cid = "xhs:seed001"
    await content_repo.upsert(make_content(cid))
    b1 = await review_repo.save_review_batch(
        cid, [_claim(cid, 1)], [], [], ReviewStatus.EXTRACTED,
    )
    b2 = await review_repo.save_review_batch(
        cid, [_claim(cid, 2)], [], [], ReviewStatus.EXTRACTED,
    )

    await review_repo.save_review_batch(
        cid, [_claim(cid, 3)], [], [], ReviewStatus.EXTRACTED,
        batch_id=b2, replace=True,
    )

    batches = {b["batch_id"]: b for b in await review_repo.list_review_batches(cid)}
    assert batches[b2]["batch_seq"] == 2       # 覆盖重试不改序号（先读序号再删行）
    assert batches[b2]["claim_count"] == 1
    assert batches[b1]["claim_count"] == 1     # 另一批次没被牵连
    assert [c.claim_id for c in await review_repo.list_claims(cid, b2)] == [f"{cid}-c3"]
    assert await review_repo.get_claim(f"{cid}-c2") is None   # 被覆盖的旧行确实清掉了


async def test_replace_without_batch_id_is_rejected(init_test_db, review_repo, content_repo):
    """replace=True 却不指名批次 = 「覆盖谁」没答案：直接报错，不静默变成追加一版。"""
    cid = "xhs:seed001"
    await content_repo.upsert(make_content(cid))
    with pytest.raises(ValueError):
        await review_repo.save_review_batch(
            cid, [_claim(cid, 1)], [], [], ReviewStatus.EXTRACTED, replace=True
        )


async def test_reports_filtered_by_content(init_test_db, review_repo, content_repo):
    """报告列表按 content_id 过滤，且不误伤前缀相同的内容 ID。"""
    a, ab = "xhs:aaa", "xhs:aaa-extra"
    await content_repo.upsert(make_content(a))
    await content_repo.upsert(make_content(ab))

    await review_repo.add_report(Report(report_id="r-a", title="A 报告", content_ids=[a]))
    await review_repo.add_report(Report(report_id="r-ab", title="AB 报告", content_ids=[ab]))
    await review_repo.add_report(Report(report_id="r-both", title="两篇", content_ids=[a, ab]))

    only_a = {r.report_id for r in await review_repo.list_reports(content_id=a)}
    assert only_a == {"r-a", "r-both"}      # 'xhs:aaa' 不能命中 'xhs:aaa-extra'

    only_ab = {r.report_id for r in await review_repo.list_reports(content_id=ab)}
    assert only_ab == {"r-ab", "r-both"}

    assert len(await review_repo.list_reports()) == 3   # 不过滤 = 全量，旧行为不变
    assert await review_repo.list_reports(content_id="xhs:nope") == []
