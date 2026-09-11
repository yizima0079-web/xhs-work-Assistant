"""知识库服务测试：chunk_text/cosine/top_k 纯函数 + ingest/dedup/检索/错误路径。

FakeEmbeddingAdapter 用确定性 token-presence 向量（CJK 单字+相邻双字+ASCII 词），
同文本共享维度，cosine 反映共现 —— 离线路由无外部调用。
"""
from __future__ import annotations

import re

import pytest

from app.adapters.embedding import (
    EmbeddingNotConfiguredError,
    EmbeddingProviderError,
)
from app.adapters.llm import ModelNotConfiguredError
from app.domain.enums import ReviewStatus
from app.domain.models import Analysis, Content, Engagement, KbDocument
from app.repositories.kb import SqliteKnowledgeBaseRepository
from app.repositories.sqlite import SqliteAnalysisRepository, SqliteContentRepository
from app.services.chunking import chunk_text
from app.services.knowledge_base import KnowledgeBaseService
from app.services.vector import cosine, top_k

_CJK = r"[一-鿿]"


def _grams(text: str) -> list[str]:
    han = re.findall(_CJK, text)
    grams = list(han)
    grams += [han[i] + han[i + 1] for i in range(len(han) - 1)]
    grams += re.findall(r"[A-Za-z0-9_]+", text)
    return grams


class FakeEmbeddingAdapter:
    def __init__(self, *, configured: bool = True, fail_all: bool = False, fail_image: bool = False):
        self.configured = configured
        self.fail_all = fail_all
        self.fail_image = fail_image
        self._dim = 256
        self._vocab: dict[str, int] = {}
        self.calls = 0

    def is_configured(self) -> bool:
        return self.configured

    async def embed(self, items: list[dict]) -> list[list[float]]:
        self.calls += 1
        if not self.configured:
            raise EmbeddingNotConfiguredError("no key")
        if self.fail_all:
            raise EmbeddingProviderError("provider boom")
        out = []
        for it in items:
            if "image" in it and self.fail_image:
                raise EmbeddingProviderError("image fail")
            text = (it.get("text") or "") + " " + (it.get("image") or "")
            vec = [0.0] * self._dim
            for tok in _grams(text):
                if tok not in self._vocab:
                    self._vocab[tok] = len(self._vocab) % self._dim
                vec[self._vocab[tok]] = 1.0
            out.append(vec)
        return out


def make_content(
    cid: str = "xhs:1",
    title: str = "AI 眼镜自费实测",
    text: str = "自费买三款 AI 眼镜对比佩戴和视力变化",
    review_status: ReviewStatus = ReviewStatus.APPROVED,
) -> Content:
    """默认造**已审核通过**的内容：知识库入库与检索有审核门禁（红线），
    未 approved 的内容会被拒绝/过滤。需要测门禁本身的用例显式传 pending。"""
    return Content(
        content_id=cid, platform="xhs", platform_item_id=cid.split(":")[-1],
        content_type="note", canonical_url=f"https://www.xiaohongshu.com/{cid}",
        author_name="测评博主", title=title, text=text,
        tags=["AI眼镜", "数码测评"], cover_url=None,
        review_status=review_status,
        engagement=Engagement(likes="12000", comments="356", shares="128", collects="900"),
    )


# ---- 纯函数：chunking / vector ----

def test_chunk_text_single_short_returns_whole():
    assert chunk_text("一句话") == ["一句话"]
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_chunk_text_cuts_at_sentence_boundary():
    text = "第一句话。第二句话。第三句话。"  # 15 字，每句 5 字含句号
    chunks = chunk_text(text, max_chars=12, overlap=2)
    assert chunks[0] == "第一句话。第二句话。"  # 前 12 字窗内切在句号后
    assert chunks[-1].endswith("。")
    # 任何块都不会把句子从中间切断（正文无残缺句尾）
    for piece in chunks:
        assert piece.endswith("。")


def test_chunk_text_hard_cut_no_boundary():
    text = "字" * 1000  # 无句读 → 硬切
    chunks = chunk_text(text, max_chars=100, overlap=0)
    assert len(chunks) == 10
    assert all(len(c) == 100 for c in chunks)
    assert "".join(chunks) == text


def test_cosine_basic():
    assert cosine([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert cosine([1.0, 0.0], [2.0, 0.0]) == pytest.approx(1.0)
    assert abs(cosine([1.0, 0.0], [0.0, 1.0])) < 1e-9
    assert cosine([1.0, 1.0], [1.0, 1.0]) == pytest.approx(1.0)
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0  # 零向量无方向


def test_top_k_ranking_and_limit():
    cands = [
        ("a", [1.0, 0.0]),
        ("b", [0.0, 1.0]),
        ("c", [0.9, 0.1]),
        ("d", []),  # 空向量跳过
    ]
    hits = top_k([1.0, 0.0], cands, k=2)
    assert [cid for _, cid in hits] == ["a", "c"]
    assert hits[0][0] == 1.0
    hits_all = top_k([1.0, 0.0], cands, k=10)
    assert [cid for _, cid in hits_all] == ["a", "c", "b"]


# ---- 服务 ingest / dedup / search ----

@pytest.fixture
def repos(init_test_db):
    return SqliteContentRepository(), SqliteAnalysisRepository(), SqliteKnowledgeBaseRepository()


def _svc(repos, adapter: FakeEmbeddingAdapter, llm=None) -> KnowledgeBaseService:
    cr, ar, kr = repos
    return KnowledgeBaseService(kr, cr, ar, adapter, llm)


async def test_vectorize_content_ready_chunks_and_dedup(repos):
    cr, ar, kr = repos
    await cr.upsert(make_content())
    svc = _svc(repos, FakeEmbeddingAdapter())
    doc = await svc.vectorize_content("xhs:1")
    assert doc.status == "ready"
    assert doc.source_type == "content" and doc.source_id == "xhs:1"
    assert doc.chunk_count > 0
    chunks = await kr.list_chunks_by_doc(doc.doc_id)
    assert len(chunks) == doc.chunk_count
    assert all(c.modality == "text" for c in chunks)
    assert chunks[0].meta["content_id"] == "xhs:1"
    assert chunks[0].meta["chunk_total"] == len(chunks)
    assert chunks[0].meta["title"] == "AI 眼镜自费实测"
    # 幂等：同源 ready → 同 doc_id，不再重复嵌入
    again = await svc.vectorize_content("xhs:1")
    assert again.doc_id == doc.doc_id
    assert (await kr.list_documents())[0].chunk_count == doc.chunk_count


async def test_vectorize_content_missing_raises_keyerror(repos):
    svc = _svc(repos, FakeEmbeddingAdapter())
    with pytest.raises(KeyError):
        await svc.vectorize_content("xhs:nope")


async def test_vectorize_analysis_enriches_from_content(repos):
    cr, ar, kr = repos
    await cr.upsert(make_content())
    await ar.add(Analysis(
        analysis_id="an-1", content_id="xhs:1", verdict="viral",
        markdown="# 作品分析（viral）\n\n**结论**：该作品因真实测评出圈。\n- 开头 3 秒直接展示佩戴效果。",
    ))
    svc = _svc(repos, FakeEmbeddingAdapter())
    doc = await svc.vectorize_analysis("an-1")
    assert doc.source_type == "analysis" and doc.source_id == "an-1"
    assert doc.status == "ready" and doc.chunk_count >= 1
    assert doc.title == "作品分析（viral）· xhs:1"
    assert doc.author == "测评博主"  # 从关联 content enrich
    chunks = await kr.list_chunks_by_doc(doc.doc_id)
    assert all(c.meta["analysis_id"] == "an-1" for c in chunks)
    assert all("作品分析" in c.text for c in chunks)


async def test_vectorize_analysis_missing_raises_keyerror(repos):
    svc = _svc(repos, FakeEmbeddingAdapter())
    with pytest.raises(KeyError):
        await svc.vectorize_analysis("an-nope")


# ---- 人工文档：两阶段（清洗暂存 pending → 向量化 ready）----

async def test_create_manual_document_cleans_to_pending_without_chunks(repos, make_llm):
    cr, ar, kr = repos
    raw = "姐妹们 2024-01-02 试了AI眼镜 真的绝了哈 喜欢的点个关注 第一款防蓝光有效 不过戴久偏重啦"
    md = "# AI 眼镜实测\n\n## 佩戴体验\n- 第一款防蓝光有效\n- 长时间佩戴偏重"
    llm = make_llm([md])
    svc = _svc(repos, FakeEmbeddingAdapter(), llm)
    doc = await svc.create_manual_document("AI 眼镜实测", raw, tags=["AI"], url="https://w/1")
    assert doc.status == "pending" and doc.source_type == "manual" and doc.source_id is None
    assert doc.chunk_count == 0
    assert doc.raw_text == raw
    assert doc.markdown == md  # 入库的是 LLM 清洗后的 markdown，绝不存未清洗原文
    assert len(llm.calls) == 1  # 模型被调用做清洗
    assert await kr.list_chunks_by_doc(doc.doc_id) == []  # 未向量化 → 无 chunk
    assert await svc.search("防蓝光") == []  # pending 无 chunk，检索不可命中


async def test_manual_document_hash_dedup_across_status(repos, make_llm):
    cr, ar, kr = repos
    raw = "同正文的重复录入内容。"
    md = "# 运营手册\n\n- 同正文去重"
    llm = make_llm([md, md, md])
    svc = _svc(repos, FakeEmbeddingAdapter(), llm)
    d1 = await svc.create_manual_document("运营手册", raw, tags=["规范"], url="https://w/1")
    d2 = await svc.create_manual_document("另一个标题", raw)  # 同清洗结果 → 同 hash → 复用 pending
    assert d2.doc_id == d1.doc_id and d2.status == "pending"
    assert len(await kr_list(repos)) == 1
    ready = await svc.vectorize_document(d1.doc_id)
    assert ready.status == "ready"
    d3 = await svc.create_manual_document("第三个标题", raw)  # ready 后同 hash 仍去重，不重复落行
    assert d3.doc_id == d1.doc_id
    assert len(await kr_list(repos)) == 1


async def test_create_manual_document_requires_title_and_text(repos, make_llm):
    svc = _svc(repos, FakeEmbeddingAdapter(), make_llm([]))
    with pytest.raises(ValueError):
        await svc.create_manual_document("  ", "正文")
    with pytest.raises(ValueError):
        await svc.create_manual_document("标题", "   ")


async def test_create_manual_without_llm_raises_not_configured(repos):
    # 无 LLM → 无法清洗 → 503，绝不把未清洗文本入库
    svc = _svc(repos, FakeEmbeddingAdapter(), llm=None)
    with pytest.raises(ModelNotConfiguredError):
        await svc.create_manual_document("标题", "正文")
    assert await repos[2].list_documents() == []


async def test_no_embedder_create_ok_but_vectorize_search_raise(repos, make_llm):
    cr, ar, kr = repos
    llm = make_llm(["# 标题\n\n正文"])
    svc = KnowledgeBaseService(kr, cr, ar, None, llm)  # embedder=None
    doc = await svc.create_manual_document("标题", "正文")
    assert doc.status == "pending"  # 清洗不依赖向量
    with pytest.raises(EmbeddingNotConfiguredError):
        await svc.vectorize_document(doc.doc_id)
    with pytest.raises(EmbeddingNotConfiguredError):
        await svc.search("查询")
    assert (await kr.get_document(doc.doc_id)).status == "pending"  # 失败不动原档


async def test_vectorize_document_pending_to_ready_chunks_and_search(repos, make_llm):
    cr, ar, kr = repos
    md = "# AI 眼镜\n\n## 翻车点\n- 鼻托压痕明显\n\n## 优点\n- 防蓝光实测有效"
    llm = make_llm([md])
    svc = _svc(repos, FakeEmbeddingAdapter(), llm)
    doc = await svc.create_manual_document("AI 眼镜", "口语化原文……", tags=["AI"])
    ready = await svc.vectorize_document(doc.doc_id)
    assert ready.doc_id == doc.doc_id
    assert ready.status == "ready" and ready.chunk_count >= 1
    assert ready.raw_text == doc.raw_text and ready.markdown == md
    chunks = await kr.list_chunks_by_doc(doc.doc_id)
    assert len(chunks) == ready.chunk_count
    assert all(c.modality == "text" for c in chunks)
    again = await svc.vectorize_document(doc.doc_id)  # ready 幂等
    assert again.doc_id == doc.doc_id
    assert (await kr.list_documents())[0].chunk_count == ready.chunk_count
    hits = await svc.search("翻车点", top_k=3)  # ready 后可检索命中
    assert hits and hits[0]["meta"].get("doc_id") == doc.doc_id


async def test_vectorize_uncleaned_empty_markdown_raises(repos, make_llm):
    cr, ar, kr = repos
    # 直接构造一条 markdown 为空的 pending（模拟未经清洗就入库的脏数据路径）
    blank = KbDocument(
        doc_id="draft-blank", source_type="manual", source_id=None, title="未清洗",
        author=None, tags=[], url=None, content_hash="h", raw_text="只有原始", markdown="",
        status="pending", chunk_count=0, created_at="2026-01-01T00:00:00+00:00",
    )
    await kr.add_document(blank)
    svc = _svc(repos, FakeEmbeddingAdapter(), make_llm([]))
    with pytest.raises(ValueError):
        await svc.vectorize_document("draft-blank")


async def test_vectorize_documents_batch_partial_failure_nonblocking(repos, make_llm):
    cr, ar, kr = repos
    llm = make_llm(["# A\n\nA 正文", "# B\n\nB 正文"])
    svc = _svc(repos, FakeEmbeddingAdapter(), llm)
    d1 = await svc.create_manual_document("A", "A 原始……")
    d2 = await svc.create_manual_document("B", "B 原始……")
    results = await svc.vectorize_documents([d1.doc_id, d2.doc_id, "missing"])
    by = {r["doc_id"]: r for r in results}
    assert by[d1.doc_id]["ok"] and by[d1.doc_id]["status"] == "ready"
    assert by[d2.doc_id]["ok"]
    assert by["missing"]["ok"] is False and by["missing"]["status"] == "failed"
    assert by["missing"]["error"]
    assert (await kr.get_document(d1.doc_id)).status == "ready"
    assert (await kr.get_document(d2.doc_id)).status == "ready"


async def test_manual_vectorize_provider_failure_preserves_markdown_then_retry(repos, make_llm):
    cr, ar, kr = repos
    md = "# 标题\n\n正文要点"
    llm = make_llm([md])
    embed = FakeEmbeddingAdapter(fail_all=True)
    svc = _svc(repos, embed, llm)
    doc = await svc.create_manual_document("标题", "原始……")
    with pytest.raises(EmbeddingProviderError):
        await svc.vectorize_document(doc.doc_id)
    after = await kr.get_document(doc.doc_id)
    assert after.status == "failed" and after.chunk_count == 0
    assert after.markdown == md and after.raw_text == doc.raw_text  # 原行与清洗结果未丢，可重试
    embed.fail_all = False
    ready = await svc.vectorize_document(doc.doc_id)
    assert ready.doc_id == doc.doc_id and ready.status == "ready"


async def test_delete_document_and_batch(repos, make_llm):
    cr, ar, kr = repos
    llm = make_llm(["# A\n\nA", "# B\n\nB", "# C\n\nC"])
    svc = _svc(repos, FakeEmbeddingAdapter(), llm)
    d1 = await svc.create_manual_document("A", "a……")
    d2 = await svc.create_manual_document("B", "b……")
    d3 = await svc.create_manual_document("C", "c……")
    await svc.vectorize_document(d1.doc_id)
    assert await svc.search("A", top_k=3)
    await svc.delete_document(d1.doc_id)
    assert await kr.get_document(d1.doc_id) is None
    assert await kr.list_chunks_by_doc(d1.doc_id) == []
    assert await svc.search("A", top_k=3) == []  # 删除后检索不再命中
    removed = await svc.delete_documents([d2.doc_id, d3.doc_id, "missing"])
    assert removed == 2
    assert len(await kr_list(repos)) == 0


async def test_provider_failure_marks_document_failed_then_rebuild(repos):
    cr, ar, kr = repos
    await cr.upsert(make_content())
    adapter = FakeEmbeddingAdapter(fail_all=True)
    svc = _svc(repos, adapter)
    with pytest.raises(EmbeddingProviderError):
        await svc.vectorize_content("xhs:1")
    failed = await kr.get_document_by_source("content", "xhs:1")
    assert failed is not None and failed.status == "failed" and failed.chunk_count == 0
    # 恢复后重建：删旧 failed、重入库 → ready，同源仅剩一条
    adapter.fail_all = False
    rebuilt = await svc.vectorize_content("xhs:1")
    assert rebuilt.status == "ready" and rebuilt.doc_id != failed.doc_id
    assert (await kr.get_document_by_source("content", "xhs:1")).doc_id == rebuilt.doc_id


async def test_cover_image_embedding_success_and_fallback(repos):
    cr, ar, kr = repos
    # 成功：text chunk + image chunk
    c = make_content(); c.cover_url = "https://img.xhs.com/covers/1.jpg"
    await cr.upsert(c)
    svc = _svc(repos, FakeEmbeddingAdapter())
    doc = await svc.vectorize_content("xhs:1")
    chunks = await kr.list_chunks_by_doc(doc.doc_id)
    imgs = [x for x in chunks if x.modality == "image"]
    assert len(imgs) == 1
    assert imgs[0].image_url == c.cover_url
    assert imgs[0].meta["modality"] == "image"
    # 图片失败 → 纯文本退路，不阻断
    await kr.delete_document(doc.doc_id)
    svc2 = _svc(repos, FakeEmbeddingAdapter(fail_image=True))
    doc2 = await svc2.vectorize_content("xhs:1")
    assert doc2.status == "ready"
    chunks2 = await kr.list_chunks_by_doc(doc2.doc_id)
    assert all(x.modality == "text" for x in chunks2)


async def test_search_returns_relevant_doc_first(repos):
    cr, ar, kr = repos
    await cr.upsert(make_content(cid="xhs:1", title="AI 眼镜自费实测", text="自费买三款 AI 眼镜对比佩戴和视力变化"))
    await cr.upsert(make_content(cid="xhs:2", title="周末公园野餐", text="天气不错带孩子去公园野餐"))
    svc = _svc(repos, FakeEmbeddingAdapter())
    await svc.vectorize_content("xhs:1")
    await svc.vectorize_content("xhs:2")
    hits = await svc.search("AI 眼镜值得买吗", top_k=3)
    assert hits and hits[0]["doc_id"] is not None
    top = hits[0]
    assert top["meta"].get("content_id") == "xhs:1"  # 相关文档排第一
    assert top["score"] > 0.0
    assert set(top) >= {"chunk_id", "doc_id", "text", "score", "modality", "meta"}
    assert "眼镜" in top["text"]


async def test_search_empty_query_returns_empty(repos):
    svc = _svc(repos, FakeEmbeddingAdapter())
    assert await svc.search("   ") == []


async def kr_list(repos):
    return await repos[2].list_documents()
