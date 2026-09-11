"""审核多版本：批次列表 / 切换生效版本 / 人工改判只影响本批次 / 迁移前历史行。

「多版本管理」的核心承诺是：重审不毁旧版，用户能挑一版生效。这里逐条锁住。
"""
from __future__ import annotations

import pytest

from app.db import connect
from app.domain.enums import ClaimStatus, ClaimType, ReviewStatus
from app.domain.models import Claim
from app.repositories.sqlite import SqliteContentRepository, SqliteReviewRepository
from app.services.review import ReviewService
from tests.test_review_repository import make_content


@pytest.fixture
def content_repo():
    return SqliteContentRepository()


@pytest.fixture
def review_repo():
    return SqliteReviewRepository()


@pytest.fixture
async def content(init_test_db, content_repo):
    c = make_content("xhs:seed001")
    await content_repo.upsert(c)
    return c


def _supported(cid: str, n: int) -> Claim:
    return Claim(
        claim_id=f"{cid}-c{n}", content_id=cid, text=f"断言 {n}",
        status=ClaimStatus.SUPPORTED, confidence=0.9, claim_type=ClaimType.FACT,
    )


def _unclear(cid: str, n: int) -> Claim:
    return Claim(
        claim_id=f"{cid}-c{n}", content_id=cid, text=f"存疑断言 {n}",
        status=ClaimStatus.UNCLEAR, confidence=0.4,
    )


def _service(content_repo, review_repo):
    return ReviewService(review_repo, content_repo, llm=None, searcher=None)


async def test_batches_listed_newest_first_with_counts(init_test_db, content, content_repo, review_repo):
    cid = content.content_id
    b1 = await review_repo.save_review_batch(
        cid, [_supported(cid, 1), _supported(cid, 2)], [], [], ReviewStatus.APPROVED
    )
    b2 = await review_repo.save_review_batch(
        cid, [_unclear(cid, 3)], [], [], ReviewStatus.EXTRACTED
    )

    batches = await review_repo.list_review_batches(cid)
    assert [b["batch_id"] for b in batches] == [b2, b1]
    assert [b["batch_seq"] for b in batches] == [2, 1]
    assert [b["claim_count"] for b in batches] == [1, 2]
    assert [b["active"] for b in batches] == [True, False]
    assert all(b["created_at"] for b in batches)


async def test_activate_old_batch_switches_content_status(init_test_db, content, content_repo, review_repo):
    """切回旧批次后，内容状态必须由**该批次**重算，而不是停留在新批次的判定上。"""
    cid = content.content_id
    b1 = await review_repo.save_review_batch(
        cid, [_supported(cid, 1)], [], [], ReviewStatus.APPROVED
    )
    b2 = await review_repo.save_review_batch(
        cid, [_unclear(cid, 2)], [], [], ReviewStatus.EXTRACTED
    )
    assert (await content_repo.get(cid)).review_status == ReviewStatus.EXTRACTED

    service = _service(content_repo, review_repo)
    status = await service.activate_review_batch(cid, b1)

    assert status == ReviewStatus.APPROVED
    got = await content_repo.get(cid)
    assert got.active_batch_id == b1
    assert got.review_status == ReviewStatus.APPROVED
    # 默认读的就是刚激活的那批
    assert [c.claim_id for c in await review_repo.list_claims(cid)] == [f"{cid}-c1"]
    # 批次列表（新→旧）里 active 落在第 1 版上，第 2 版仍在、只是不再生效
    assert [b["active"] for b in await review_repo.list_review_batches(cid)] == [False, True]
    assert b2 != b1


async def test_activate_unknown_batch_raises(init_test_db, content, content_repo, review_repo):
    service = _service(content_repo, review_repo)
    with pytest.raises(KeyError):
        await service.activate_review_batch(content.content_id, "not-a-batch")


async def test_activate_foreign_batch_is_rejected(init_test_db, content, content_repo, review_repo):
    """别的内容的批次 id 不能拿来激活：set_active_batch 会先验证归属。"""
    other = make_content("xhs:other")
    await content_repo.upsert(other)
    foreign = await review_repo.save_review_batch(
        other.content_id, [_supported(other.content_id, 1)], [], [], ReviewStatus.APPROVED
    )
    service = _service(content_repo, review_repo)
    with pytest.raises(KeyError):
        await service.activate_review_batch(content.content_id, foreign)
    assert (await content_repo.get(content.content_id)).active_batch_id is None


async def test_human_decide_only_recomputes_its_own_batch(
    init_test_db, content, content_repo, review_repo
):
    """改判一条断言只按**它所属批次**重算内容状态。

    版本化后若仍用 list_claims(content_id) 取全部批次，另一批的断言会被算进来，
    状态就会串版 —— 这是本次必须锁住的回归点。
    """
    cid = content.content_id
    # 第 1 版：两条 supported（approved）。第 2 版：一条 supported（approved）
    b1 = await review_repo.save_review_batch(
        cid, [_supported(cid, 1), _supported(cid, 2)], [], [], ReviewStatus.APPROVED
    )
    b2 = await review_repo.save_review_batch(
        cid, [_supported(cid, 3)], [], [], ReviewStatus.APPROVED
    )
    service = _service(content_repo, review_repo)

    # 在**生效批次**（第 2 版）里把唯一一条改成 contradicted → rejected
    status = await service.human_decide(f"{cid}-c3", ClaimStatus.CONTRADICTED, rationale="人工推翻")
    assert status == ReviewStatus.REJECTED
    assert (await content_repo.get(cid)).review_status == ReviewStatus.REJECTED
    # 第 1 版的断言完全没被牵连
    assert {c.status for c in await review_repo.list_claims(cid, b1)} == {ClaimStatus.SUPPORTED}

    # 切到第 1 版，再在**非生效批次**里改一条：内容状态不该被历史版本带走
    await service.activate_review_batch(cid, b1)
    assert (await content_repo.get(cid)).review_status == ReviewStatus.APPROVED
    await service.human_decide(f"{cid}-c3", ClaimStatus.SUPPORTED, rationale="改回")
    assert (await content_repo.get(cid)).review_status == ReviewStatus.APPROVED


async def test_get_content_review_returns_all_batches_and_defaults_to_active(
    init_test_db, content, content_repo, review_repo
):
    cid = content.content_id
    b1 = await review_repo.save_review_batch(
        cid, [_supported(cid, 1)], [], [], ReviewStatus.APPROVED
    )
    b2 = await review_repo.save_review_batch(
        cid, [_supported(cid, 2)], [], [], ReviewStatus.APPROVED
    )
    service = _service(content_repo, review_repo)

    detail = await service.get_content_review(cid)
    assert detail["batch_id"] == b2
    assert [c["claim"].claim_id for c in detail["claims"]] == [f"{cid}-c2"]
    assert [b["batch_id"] for b in detail["batches"]] == [b2, b1]

    # 指定批次
    detail1 = await service.get_content_review(cid, batch_id=b1)
    assert detail1["batch_id"] == b1
    assert [c["claim"].claim_id for c in detail1["claims"]] == [f"{cid}-c1"]


async def test_legacy_rows_without_batch_behave_as_first_version(
    init_test_db, content, content_repo, review_repo
):
    """迁移前的历史行（batch_id='' / batch_seq=1）天然就是「第 1 版」，无需回填。"""
    cid = content.content_id
    await review_repo.add_claim(
        Claim(claim_id=f"{cid}-legacy", content_id=cid, text="迁移前的断言")
    )
    batches = await review_repo.list_review_batches(cid)
    assert len(batches) == 1
    assert batches[0]["batch_id"] == ""
    assert batches[0]["batch_seq"] == 1
    assert batches[0]["active"] is True
    assert [c.claim_id for c in await review_repo.list_claims(cid)] == [f"{cid}-legacy"]

    # 在其之上重审：旧行成为第 1 版，新批次是第 2 版
    b2 = await review_repo.save_review_batch(
        cid, [_supported(cid, 1)], [], [], ReviewStatus.APPROVED
    )
    assert [b["batch_seq"] for b in await review_repo.list_review_batches(cid)] == [2, 1]
    assert [c.claim_id for c in await review_repo.list_claims(cid, "")] == [f"{cid}-legacy"]
    assert [c.claim_id for c in await review_repo.list_claims(cid, b2)] == [f"{cid}-c1"]


async def test_list_claims_without_any_batch_is_empty(init_test_db, content, review_repo):
    """没有任何批次时返回空，而不是「回落成全部批次」冒充生效版本。"""
    assert await review_repo.list_claims(content.content_id) == []
    assert await review_repo.list_review_batches(content.content_id) == []


async def test_migration_adds_batch_columns_idempotently(init_test_db):
    """列迁移幂等：重复 init_db 不报错，列都在（真库升级路径）。"""
    from app.db import init_db

    await init_db()
    await init_db()
    async with connect() as db:
        cur = await db.execute("PRAGMA table_info(claims)")
        claim_cols = {dict(r)["name"] for r in await cur.fetchall()}
        cur = await db.execute("PRAGMA table_info(contents)")
        content_cols = {dict(r)["name"] for r in await cur.fetchall()}
    assert {"batch_id", "batch_seq"} <= claim_cols
    assert "active_batch_id" in content_cols


async def test_init_db_upgrades_a_pre_versioning_database(monkeypatch, tmp_path):
    """老库升级路径：claims 表里根本没有 batch_* 列时，init_db 也必须能跑完。

    这是线上真库的实际形态，也是本用例存在的理由：把建在迁移列上的索引写进
    schema.sql，老库会在 executescript 阶段直接 "no such column: batch_seq"，
    整个服务启动失败 —— 而只对新库建表跑测试是发现不了的。
    """
    import sqlite3

    from app.config import settings
    from app.db import init_db

    db_path = tmp_path / "legacy.db"
    monkeypatch.setattr(settings, "db_path", db_path)

    # 造一个「版本化之前」的最小库：claims 只有老列，没有 batch_id / batch_seq
    legacy = sqlite3.connect(db_path)
    legacy.executescript(
        """
        CREATE TABLE contents (
            content_id TEXT PRIMARY KEY,
            platform TEXT NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE TABLE claims (
            claim_id TEXT PRIMARY KEY,
            content_id TEXT NOT NULL,
            text TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'unverified',
            confidence REAL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        INSERT INTO claims VALUES ('c1', 'x', '旧断言', 'supported', 0.9, 't', 't');
        """
    )
    legacy.commit()
    legacy.close()

    await init_db()

    async with connect() as db:
        cur = await db.execute("PRAGMA table_info(claims)")
        cols = {dict(r)["name"] for r in await cur.fetchall()}
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name='idx_claims_content_batch'"
        )
        index = await cur.fetchone()
        cur = await db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND name IN ('idx_claims_batch', 'idx_claims_content')"
        )
        stale = await cur.fetchall()
        cur = await db.execute("SELECT batch_seq, batch_id FROM claims WHERE claim_id='c1'")
        row = dict(await cur.fetchone())

    assert {"batch_id", "batch_seq"} <= cols
    assert index is not None, "迁移列上的索引必须在补列之后建出来"
    assert not stale, "被复合索引取代的窄索引必须被 DROP 掉"
    # 旧行无需回填：默认值就让它自然成为「第 1 版」
    assert row["batch_seq"] == 1 and row["batch_id"] == ""
