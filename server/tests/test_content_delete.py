"""内容池删除：级联清干净、不误伤邻居、报告摘除该 id、本地封面文件删除。

删除不可逆，所以测试重点是「删干净了」与「没多删」两侧都要成立。
"""
from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from app.config import settings
from app.db import connect
from app.domain.enums import ClaimStatus, ClaimType, EvidenceKind, ReviewStatus
from app.domain.models import (
    Analysis,
    Claim,
    Evidence,
    RawAsset,
    Report,
    ReviewDecision,
)
from app.main import create_app
from app.repositories.sqlite import (
    SqliteAnalysisRepository,
    SqliteContentRepository,
    SqliteRawAssetRepository,
    SqliteReviewRepository,
)
from app.services.content import ContentService
from app.services.media import delete_cover
from tests.test_review_repository import make_content

CID = "xhs:subject"
OTHER = "xhs:keep"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _count(table: str, where: str, args: tuple) -> int:
    async with connect() as db:
        cur = await db.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", args)
        return (await cur.fetchone())[0]


async def _seed_content(cid: str, with_kb: bool = True) -> None:
    """造一条「什么都有」的内容：断言+证据+判定 / 分析 / KB 文档与切片 / 原始层。"""
    content_repo = SqliteContentRepository()
    review_repo = SqliteReviewRepository()
    analysis_repo = SqliteAnalysisRepository()

    await content_repo.upsert(make_content(cid))
    await review_repo.save_review_batch(
        cid,
        [Claim(claim_id=f"{cid}-c1", content_id=cid, text="断言", status=ClaimStatus.SUPPORTED,
               confidence=0.9, claim_type=ClaimType.FACT)],
        [Evidence(evidence_id=f"{cid}-e1", claim_id=f"{cid}-c1", source_kind=EvidenceKind.CONTENT,
                  source_ref="xhs:doc1", excerpt="证据", supports=True)],
        [ReviewDecision(decision_id=f"{cid}-d1", claim_id=f"{cid}-c1",
                        status=ClaimStatus.SUPPORTED, rationale="自动")],
        ReviewStatus.APPROVED,
    )
    await analysis_repo.add(
        Analysis(analysis_id=f"{cid}-a1", content_id=cid, verdict="viral", markdown="# 分析")
    )
    await SqliteRawAssetRepository().add(
        RawAsset(id=f"{cid}-raw1", run_id="run-1", content_id=cid, kind="note")
    )
    if not with_kb:
        return
    async with connect() as db:
        # 两个来源各一篇文档：content 直配该内容；analysis 由该内容的分析派生
        for doc_id, src_type, src_id in (
            (f"{cid}-doc1", "content", cid),
            (f"{cid}-doc2", "analysis", f"{cid}-a1"),
        ):
            await db.execute(
                "INSERT INTO kb_documents (doc_id, source_type, source_id, doc_type, title, tags,"
                " content_hash, raw_text, markdown, status, chunk_count, created_at)"
                " VALUES (?,?,?,?,?,'[]',?,?,?,'ready',1,?)",
                (doc_id, src_type, src_id, "general", f"文档 {doc_id}", f"h-{doc_id}", "", "# 正文", _now()),
            )
            await db.execute(
                "INSERT INTO kb_chunks (chunk_id, doc_id, chunk_index, text, embedding, modality,"
                " meta, created_at) VALUES (?,?,0,?,'[]','text','{}',?)",
                (f"{doc_id}-chunk0", doc_id, "切片正文", _now()),
            )
        await db.execute(
            "INSERT INTO kb_qa_pairs (qa_id, doc_id, qa_index, question, answer, dimensions,"
            " evidence, tags, source_type, status, created_at, updated_at)"
            " VALUES (?,?,0,?,?,'{}','[]','[]','manual','approved',?,?)",
            (f"{cid}-qa1", f"{cid}-doc2", "问题？", "答案。", _now(), _now()),
        )
        await db.commit()


async def test_delete_cascades_all_derived_rows(init_test_db):
    await _seed_content(CID)
    counts = await SqliteContentRepository().delete_content(CID)

    assert counts is not None
    assert counts["claims"] == 1
    assert counts["evidence"] == 1
    assert counts["review_decisions"] == 1
    assert counts["analyses"] == 1
    assert counts["raw_assets"] == 1
    assert counts["kb_documents"] == 2
    assert counts["kb_chunks"] == 2
    assert counts["kb_qa_pairs"] == 1
    assert counts["contents"] == 1

    # 逐表复核：该内容相关的行数必须归零
    assert await _count("contents", "content_id=?", (CID,)) == 0
    assert await _count("claims", "content_id=?", (CID,)) == 0
    assert await _count("evidence", "claim_id LIKE ?", (f"{CID}%",)) == 0
    assert await _count("review_decisions", "claim_id LIKE ?", (f"{CID}%",)) == 0
    assert await _count("analyses", "content_id=?", (CID,)) == 0
    assert await _count("raw_assets", "content_id=?", (CID,)) == 0
    assert await _count("kb_documents", "doc_id LIKE ?", (f"{CID}%",)) == 0
    assert await _count("kb_chunks", "doc_id LIKE ?", (f"{CID}%",)) == 0
    assert await _count("kb_qa_pairs", "qa_id LIKE ?", (f"{CID}%",)) == 0


async def test_delete_removes_id_from_reports_and_drops_empty(init_test_db):
    """报告无 FK：只从 content_ids 里摘掉该 id，摘空才整篇删。"""
    review_repo = SqliteReviewRepository()
    await _seed_content(CID, with_kb=False)
    await review_repo.add_report(
        Report(report_id="rep-mix", title="两篇", content_ids=[CID, OTHER])
    )
    await review_repo.add_report(Report(report_id="rep-only", title="仅本篇", content_ids=[CID]))

    await SqliteContentRepository().delete_content(CID)

    mixed = await review_repo.get_report("rep-mix")
    assert mixed is not None and mixed.content_ids == [OTHER]  # 保留，只摘掉被删的
    assert await review_repo.get_report("rep-only") is None     # 摘空 → 整篇删除


async def test_delete_leaves_other_content_untouched(init_test_db):
    await _seed_content(CID)
    await _seed_content(OTHER)

    counts = await SqliteContentRepository().delete_content(CID)
    assert counts["contents"] == 1

    # 邻居的每一层都还在
    assert await _count("contents", "content_id=?", (OTHER,)) == 1
    assert await _count("claims", "content_id=?", (OTHER,)) == 1
    assert await _count("evidence", "claim_id=?", (f"{OTHER}-c1",)) == 1
    assert await _count("analyses", "content_id=?", (OTHER,)) == 1
    assert await _count("kb_documents", "doc_id=?", (f"{OTHER}-doc1",)) == 1
    assert await _count("kb_chunks", "doc_id=?", (f"{OTHER}-doc1",)) == 1
    assert await _count("kb_qa_pairs", "doc_id=?", (f"{OTHER}-doc2",)) == 1


async def test_delete_unknown_content_returns_none(init_test_db):
    """内容不存在返回 None：让路由 404，不把「什么都没删」当成功。"""
    assert await SqliteContentRepository().delete_content("xhs:ghost") is None


async def test_delete_removes_cover_file(init_test_db, monkeypatch, tmp_path):
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    cover = media_dir / "note1.jpg"
    cover.write_bytes(b"fake-jpeg")
    monkeypatch.setattr(settings, "media_dir", media_dir)

    content_repo = SqliteContentRepository()
    content = make_content(CID)
    content.cover_local = "/media/note1.jpg"
    await content_repo.upsert(content)

    counts = await ContentService(content_repo, settings).delete_content(CID)

    assert counts is not None and counts["cover_deleted"] == 1
    assert not cover.exists()


async def test_delete_missing_cover_file_does_not_fail(init_test_db, monkeypatch, tmp_path):
    """封面文件已经不在（孤儿记录）：删除照常成功，只是不计 cover_deleted。"""
    monkeypatch.setattr(settings, "media_dir", tmp_path / "media")
    content_repo = SqliteContentRepository()
    content = make_content(CID)
    content.cover_local = "/media/gone.jpg"
    await content_repo.upsert(content)

    counts = await ContentService(content_repo, settings).delete_content(CID)

    assert counts is not None
    assert counts["contents"] == 1
    assert "cover_deleted" not in counts


def test_delete_cover_rejects_path_traversal(tmp_path):
    """cover_local 被写成越界路径时只当普通文件名处理，不碰 media_dir 之外的文件。"""
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"must-not-be-deleted")

    assert delete_cover("../secret.txt", media_dir) is False
    assert outside.exists()
    assert delete_cover(None, media_dir) is False
    assert delete_cover("", media_dir) is False


async def test_delete_endpoint_204_and_404(init_test_db, make_adapter, make_llm):
    app = create_app(adapter=make_adapter(), llm_adapter=make_llm([]))
    async with app.router.lifespan_context(app):
        await _seed_content(CID, with_kb=False)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            r = await client.delete(f"/api/v1/contents/{CID}")
            assert r.status_code == 204, r.text
            assert (await client.get(f"/api/v1/contents/{CID}")).status_code == 404
            # 再删一次：内容已不在 → 404，不静默成功
            assert (await client.delete(f"/api/v1/contents/{CID}")).status_code == 404
