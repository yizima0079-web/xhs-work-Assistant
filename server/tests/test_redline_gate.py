"""红线：未审核内容永不进 kb_chunks —— 入库门禁 + 检索过滤。

两个层次各有一道：

- **入库门禁**（`vectorize_content` / `vectorize_analysis`）：未 approved 拒绝，409。
  这是状态判断而非永久拉黑 —— 补审核到 approved 后即可正常入库。
- **检索过滤**（`iter_retrievable_chunks`）：即便数据里已经混进越线 chunk
  （历史数据、或绕过门禁直连写库），检索视图也必须把它们排除干净。
  这是「堵门禁、不删数据」方案的核心保证。

真实库上那条历史越线文档（`2f58caafdd03…`）就是靠第二道防线被挡住的，
见 `tools/verify_schema.py` 的红线行为层检查。
"""
from __future__ import annotations

import httpx
import pytest

from app.domain.enums import ReviewStatus
from app.domain.models import Content, KbChunk, KbDocument
from app.main import create_app
from app.repositories.kb import SqliteKnowledgeBaseRepository
from app.repositories.sqlite import (
    SqliteAnalysisRepository,
    SqliteContentRepository,
)
from app.services.knowledge_base import KnowledgeBaseService
from tests.test_kb_service import FakeEmbeddingAdapter as FakeEmbedding

TEXT = "独一无二的冷门话题文本用于检索命中"


def _service(emb):
    return KnowledgeBaseService(
        SqliteKnowledgeBaseRepository(), SqliteContentRepository(),
        SqliteAnalysisRepository(), emb, None,
    )


async def _put_content(cid: str, status: ReviewStatus) -> None:
    await SqliteContentRepository().upsert(Content(
        content_id=cid, platform="xhs", platform_item_id=cid.split(":")[-1],
        title=TEXT, text=TEXT, review_status=status,
    ))


async def _plant_chunk(emb, doc_id: str, source_type: str, source_id: str) -> None:
    """绕过入库门禁直接落一份已向量化文档 —— 模拟历史脏数据。"""
    repo = SqliteKnowledgeBaseRepository()
    await repo.add_document(KbDocument(
        doc_id=doc_id, source_type=source_type, source_id=source_id,
        title=TEXT, content_hash=f"h-{doc_id}",
    ))
    vec = (await emb.embed([{"text": TEXT}]))[0]
    await repo.add_chunks([KbChunk(
        chunk_id=f"c-{doc_id}", doc_id=doc_id, chunk_index=0,
        text=TEXT, embedding=vec, meta={"kind": "doc"},
    )])
    await repo.set_document_state(doc_id, "ready", 1)


# ------------------------------------------------- 入库门禁（服务层）

async def test_vectorize_pending_content_rejected(init_test_db):
    emb = FakeEmbedding()
    await _put_content("xhs:pending", ReviewStatus.PENDING)
    with pytest.raises(Exception) as exc:
        await _service(emb).vectorize_content("xhs:pending")
    assert "审核" in str(exc.value)
    # 拒绝意味着**一点向量都没落**，而不是落了再删
    assert emb.calls == 0
    assert await SqliteKnowledgeBaseRepository().get_document_by_source(
        "content", "xhs:pending"
    ) is None


async def test_vectorize_approved_content_ok(init_test_db):
    emb = FakeEmbedding()
    await _put_content("xhs:ok", ReviewStatus.APPROVED)
    doc = await _service(emb).vectorize_content("xhs:ok")
    assert doc.status == "ready" and doc.chunk_count > 0


@pytest.mark.parametrize("status", [
    ReviewStatus.PENDING, ReviewStatus.EXTRACTED,
    ReviewStatus.REJECTED, ReviewStatus.DISPUTED,
])
async def test_gate_covers_every_non_approved_status(init_test_db, status):
    """只有 approved 放行 —— extracted / disputed 这类「待人工」状态同样不算通过。"""
    emb = FakeEmbedding()
    cid = f"xhs:{status.value}"
    await _put_content(cid, status)
    with pytest.raises(Exception):
        await _service(emb).vectorize_content(cid)
    assert emb.calls == 0


async def test_gate_is_state_based_not_blacklist(init_test_db):
    """门禁看的是**当前状态**：pending 被拒，改成 approved 后同一内容即可入库。"""
    emb = FakeEmbedding()
    await _put_content("xhs:flip", ReviewStatus.PENDING)
    with pytest.raises(Exception):
        await _service(emb).vectorize_content("xhs:flip")

    await SqliteContentRepository().update_review_status("xhs:flip", ReviewStatus.APPROVED)
    doc = await _service(emb).vectorize_content("xhs:flip")
    assert doc.status == "ready"


async def test_analysis_gate_follows_source_content(init_test_db):
    """分析产物依附源内容的审核结论：源内容未 approved → 分析也不得入库。"""
    from app.domain.models import Analysis

    emb = FakeEmbedding()
    await _put_content("xhs:src", ReviewStatus.PENDING)
    await SqliteAnalysisRepository().add(
        Analysis(analysis_id="aid-x", content_id="xhs:src", markdown="# 分析正文")
    )
    with pytest.raises(Exception) as exc:
        await _service(emb).vectorize_analysis("aid-x")
    assert "审核" in str(exc.value)
    assert emb.calls == 0


# ------------------------------------------------- 检索过滤（行为层）

async def test_search_excludes_unapproved_content_chunks(init_test_db):
    """决定性：脏 chunk 已在库里，检索仍命中不到。"""
    emb = FakeEmbedding()
    await _put_content("xhs:bad", ReviewStatus.PENDING)
    await _plant_chunk(emb, "d-bad", "content", "xhs:bad")

    hits = await _service(emb).search(TEXT, top_k=5)

    assert hits == [], f"未审核内容的 chunk 被检索到了: {hits}"


async def test_search_excludes_analysis_of_unapproved_content(init_test_db):
    """两级 JOIN：分析文档要回溯到源内容的审核状态。"""
    emb = FakeEmbedding()
    await _put_content("xhs:bad", ReviewStatus.PENDING)
    await SqliteAnalysisRepository().add(
        _analysis("aid-bad", "xhs:bad")
    )
    await _plant_chunk(emb, "d-bad-a", "analysis", "aid-bad")

    hits = await _service(emb).search(TEXT, top_k=5)

    assert hits == [], f"未审核内容的分析 chunk 被检索到了: {hits}"


async def test_search_excludes_chunk_with_dangling_source(init_test_db):
    """source_id 悬空（找不到源内容/分析）→ 审核状态未知 → 默认拒绝。"""
    emb = FakeEmbedding()
    await _plant_chunk(emb, "d-orphan", "content", "xhs:does-not-exist")

    hits = await _service(emb).search(TEXT, top_k=5)

    assert hits == []


async def test_search_includes_after_approval(init_test_db):
    """门禁是状态判断：内容转 approved 后，同一份数据立刻可被检索到。"""
    emb = FakeEmbedding()
    await _put_content("xhs:flip", ReviewStatus.PENDING)
    await _plant_chunk(emb, "d-flip", "content", "xhs:flip")
    assert await _service(emb).search(TEXT, top_k=5) == []

    await SqliteContentRepository().update_review_status("xhs:flip", ReviewStatus.APPROVED)
    hits = await _service(emb).search(TEXT, top_k=5)

    assert [h["doc_id"] for h in hits] == ["d-flip"]


async def test_search_still_includes_manual_documents(init_test_db):
    """人工录入（manual）是终态，不因审核门禁被误伤。"""
    emb = FakeEmbedding()
    await _plant_chunk(emb, "d-manual", "manual", None)

    hits = await _service(emb).search(TEXT, top_k=5)

    assert [h["doc_id"] for h in hits] == ["d-manual"]


# ------------------------------------------------- 入库门禁的 HTTP 映射

async def test_http_vectorize_pending_content_409(init_test_db, make_adapter):
    await _put_content("xhs:pending", ReviewStatus.PENDING)
    app = create_app(adapter=make_adapter(), embedding_adapter=FakeEmbedding())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            resp = await client.post("/api/v1/kb/contents/xhs:pending")
    assert resp.status_code == 409, resp.text
    assert "审核" in resp.json()["detail"]


async def test_http_vectorize_approved_content_200(init_test_db, make_adapter):
    await _put_content("xhs:ok", ReviewStatus.APPROVED)
    app = create_app(adapter=make_adapter(), embedding_adapter=FakeEmbedding())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as client:
            resp = await client.post("/api/v1/kb/contents/xhs:ok")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ready"


def _analysis(analysis_id: str, content_id: str):
    from app.domain.models import Analysis

    return Analysis(analysis_id=analysis_id, content_id=content_id, markdown="# 分析")
