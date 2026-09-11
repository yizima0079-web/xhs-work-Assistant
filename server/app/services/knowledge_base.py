"""知识库服务：两阶段入库 + 检索。

入库分两类：
- 系统产物（content/analysis）：已结构化，`vectorize_*` 一步到位 embedding→ready。
- 人工文档（manual）：先 `create_manual_document` —— LLM 清洗 raw_text→markdown，
  只存 `status=pending`（**不向量化、不检索**，前端可预览前 100 行）；随后
  `vectorize_document`/`vectorize_documents` 逐个/批量把 markdown 分块嵌入后置 ready。
  `content_hash = sha256(markdown)`：同 markdown 幂等去重（pending 或 ready 都返回旧文档）。

- dedup：content/analysis 按 (source_type, source_id) + ready 幂等；manual 按 hash。
- 无 key / 未配置：清洗缺 LLM → ModelNotConfiguredError(503)；向量化缺 embedder →
  EmbeddingNotConfiguredError(503)。provider 失败把文档标 failed 后上抛，绝不伪造向量。
- 封面图嵌入是可选的"图片模态"：单独一次请求，失败即退化为纯文本 chunk，不阻断。
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

from app.adapters.embedding import (
    EmbeddingAdapter,
    EmbeddingError,
    EmbeddingNotConfiguredError,
    EmbeddingParseError,
    EmbeddingProviderError,
)
from app.adapters.llm import ModelAdapter, ModelNotConfiguredError
from app.domain.models import KbChunk, KbDocument, KbQaPair
from app.repositories.base import AnalysisRepository, ContentRepository, KnowledgeBaseRepository
from app.services.chunking import chunk_text
from app.services.markdown_cleaner import clean as clean_markdown
from app.services.vector import cosine as _cosine
from app.services.vector import top_k as _rank_top_k

_ENG_LABEL = {"likes": "点赞", "comments": "评论", "shares": "转发", "collects": "收藏"}

# Q&A 路径刻意绕过 chunk_text：按 500 字符机械切分会把问题与答案拆到两个 chunk，
# 检索时命中答案却看不到问题（或反之）。每对问答固定一个 chunk，只有尾部
# 迁移建议/风险段可截断，问题与答案永不截断。
_QA_CHUNK_MAX = 1500
_QA_EMBED_BATCH = 10
_QA_BOOST_CANDIDATES = 40  # 开启 qa_boost 时先放大候选集再重排，降低截断误差


class ReviewGateError(ValueError):
    """内容未审核通过，不得进知识库（红线：未审核内容永不进 kb_chunks）。

    继承 ValueError 以便 `vectorize_documents` 的既有 except 分支照常逐条回执；
    路由层在 ValueError→422 之前单独捕获它并映射 409（状态冲突，非校验失败）。
    """


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _content_body(content) -> str:
    """把一条已采内容拼成可向量化文本（标题/正文/标签/作者/互动）。"""
    lines: list[str] = []
    if content.title:
        lines.append(f"标题：{content.title}")
    if content.text:
        lines.append(f"正文：{content.text}")
    if content.tags:
        lines.append(f"标签：{' '.join(content.tags)}")
    if content.author_name:
        lines.append(f"作者：{content.author_name}")
    eng = content.engagement.model_dump() if content.engagement else {}
    eng_items = [f"{_ENG_LABEL.get(k, k)} {v}" for k, v in eng.items() if v is not None]
    if eng_items:
        lines.append("互动：" + "，".join(eng_items))
    if content.published_at:
        lines.append(f"发布时间:{content.published_at[:10]}")
    return "\n".join(lines)


def _qa_chunk_text(pair: KbQaPair) -> str:
    """一对问答 = 一个 chunk 的文本。问题与答案永不截断，只截尾部的迁移建议/风险段。"""
    head = f"问题：{pair.question}\n\n答案：{pair.answer}"
    dims = pair.dimensions or {}
    tail_parts: list[str] = []
    dim_lines = [
        f"{label}：{dims[key]}"
        for key, label in (
            ("technique", "表现手法"), ("persona", "IP 人设"),
            ("hook", "开头钩子"), ("structure", "结构节奏"),
        )
        if dims.get(key)
    ]
    if dim_lines:
        tail_parts.append("\n".join(dim_lines))
    for key, label in (("transfer", "迁移建议"), ("transfer_risk", "失败风险")):
        items = dims.get(key) or []
        if items:
            tail_parts.append(f"{label}：" + "；".join(str(i) for i in items))
    if pair.tags:
        tail_parts.append("标签：" + " ".join(pair.tags))
    tail = "\n\n".join(tail_parts)
    budget = _QA_CHUNK_MAX - len(head) - 2
    return head if budget <= 0 else f"{head}\n\n{tail[:budget]}"


def _base_meta(*, doc_id: str, source_type: str, source_id: str | None,
               title: str, author: str | None, tags: list[str], url: str | None) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "doc_id": doc_id,
        "source_type": source_type,
        "title": title,
        "author": author,
        "tags": tags,
        "url": url,
    }
    if source_type == "content":
        meta["content_id"] = source_id
    elif source_type == "analysis":
        meta["analysis_id"] = source_id
    return meta


class KnowledgeBaseService:
    """向量知识库编排：两阶段入库（pending→ready）+ 检索。embedder/llm=None 视为未配置。"""

    def __init__(
        self,
        kb_repo: KnowledgeBaseRepository,
        content_repo: ContentRepository,
        analysis_repo: AnalysisRepository,
        embedder: EmbeddingAdapter | None,
        llm: ModelAdapter | None = None,
        qa_boost: float = 0.0,
    ):
        self.kb_repo = kb_repo
        self.content_repo = content_repo
        self.analysis_repo = analysis_repo
        self.embedder = embedder
        self.llm = llm
        # Q&A chunk 同分优先权重。默认 0.0 = 关闭：此时排序与切片和引入 Q&A 前逐字节一致。
        self.qa_boost = qa_boost

    # ---- 人工文档：两阶段 ----

    async def create_manual_document(
        self, title: str, raw_text: str, tags: list[str] | None = None, url: str | None = None
    ) -> KbDocument:
        """清洗并落一份 pending 文档（未向量化）。无 LLM → 503；不返回未清洗文本。"""
        title = (title or "").strip()
        if not title:
            raise ValueError("文档标题不能为空")
        raw = (raw_text or "").strip()
        if not raw:
            raise ValueError("正文不能为空")

        markdown = await clean_markdown(raw, self.llm)  # ModelNotConfigured 透出 → 503
        content_hash = _sha256(markdown)

        existing = await self.kb_repo.get_document_by_hash(content_hash)
        if existing is not None:
            return existing  # 同 markdown 幂等：pending 或 ready 都复用

        created_at = _now()
        doc = KbDocument(
            doc_id=uuid.uuid4().hex, source_type="manual", source_id=None, title=title,
            author=None, tags=tags or [], url=url, content_hash=content_hash,
            raw_text=raw, markdown=markdown, status="pending", chunk_count=0,
            created_at=created_at,
        )
        await self.kb_repo.add_document(doc)
        return doc

    async def vectorize_document(self, doc_id: str) -> KbDocument:
        """把 pending 文档的 markdown 分块向量化 → ready。已 ready 直接返回。

        只嵌改原行（doc_id/content_hash/raw_text/markdown 保留），失败置 failed 并上抛；
        绝不删除原文档 —— provider 失败后 raw/markdown 仍留存，可重试向量化。
        """
        doc = await self.kb_repo.get_document(doc_id)
        if doc is None:
            raise KeyError(f"文档不存在: {doc_id}")
        if doc.doc_type == "qa":
            return await self.vectorize_qa_document(doc_id)  # 前端既有按钮对 Q&A 自动生效
        if doc.status == "ready":
            return doc
        # 纵深防御：doc 级路径目前只产生 manual 文档（create_manual_document 写死
        # source_type="manual"），但若日后扩展到 content/analysis，这里必须同样挡住，
        # 否则批量入口会成为绕开 vectorize_content 门禁的第二条路。
        if doc.source_type == "content" and doc.source_id:
            src = await self.content_repo.get(doc.source_id)
            if src is None:
                raise ReviewGateError(f"文档 {doc_id} 的源内容 {doc.source_id} 不存在，不得向量化")
            self._assert_reviewable(src, f"文档 {doc_id} 的源内容")
        body = (doc.markdown or "").strip()
        if not body:
            raise ValueError(f"文档尚未完成清洗（{doc_id}），不能向量化")
        if self.embedder is None or not self.embedder.is_configured():
            raise EmbeddingNotConfiguredError("服务端未配置向量 API key，无法入库知识库")

        base_meta = _base_meta(
            doc_id=doc.doc_id, source_type="manual", source_id=None,
            title=doc.title, author=doc.author, tags=doc.tags, url=doc.url,
        )
        try:
            rows, _total = await self._embed_rows(
                doc.doc_id, body, base_meta, doc.title, None, doc.created_at
            )
            await self.kb_repo.add_chunks(rows)
            await self.kb_repo.set_document_state(doc.doc_id, "ready", len(rows))
        except EmbeddingError as exc:
            await self.kb_repo.set_document_state(doc.doc_id, "failed", 0)
            raise
        return await self._require_doc(doc.doc_id)

    async def vectorize_documents(self, doc_ids: list[str]) -> list[dict[str, Any]]:
        """批量向量化：单条失败不阻断其余。返回逐条结果（doc/ok/status/error）。"""
        out: list[dict[str, Any]] = []
        for did in doc_ids:
            try:
                doc = await self.vectorize_document(did)
                out.append({"doc_id": did, "ok": True, "status": doc.status, "doc": doc})
            except (KeyError, ValueError, ModelNotConfiguredError, EmbeddingError) as exc:
                out.append({"doc_id": did, "ok": False, "status": "failed", "error": str(exc)})
        return out

    # ---- Q&A 文档：审核闸门 + 每对一 chunk ----

    async def list_qa_pairs(
        self, doc_id: str, app_visible_only: bool = False
    ) -> list[KbQaPair]:
        return await self.kb_repo.list_qa_pairs(doc_id, app_visible_only=app_visible_only)

    async def get_qa_pair(self, qa_id: str) -> KbQaPair | None:
        return await self.kb_repo.get_qa_pair(qa_id)

    async def update_qa_pair(
        self,
        qa_id: str,
        *,
        question: str | None = None,
        answer: str | None = None,
        dimensions: dict[str, Any] | None = None,
        tags: list[str] | None = None,
    ) -> KbQaPair:
        """人工编辑问答对。已 approved 的内容被改写 → 回落 draft（需重新审核与向量化）。"""
        pair = await self.kb_repo.get_qa_pair(qa_id)
        if pair is None:
            raise KeyError(f"问答对不存在: {qa_id}")

        touched = False
        if question is not None and question.strip() != pair.question:
            pair.question = question.strip()
            touched = True
        if answer is not None and answer.strip() != pair.answer:
            pair.answer = answer.strip()
            touched = True
        if dimensions is not None and dimensions != pair.dimensions:
            pair.dimensions = dimensions
            touched = True
        if tags is not None and tags != pair.tags:
            pair.tags = tags
            touched = True

        if not touched:
            return pair
        if pair.status == "approved":
            pair.status = "draft"  # 人工审核过的内容被机器/人改写 → 回到待审，不静默生效
        pair.updated_at = _now()
        await self.kb_repo.update_qa_pair(pair)
        await self._touch_qa_doc(pair.doc_id)
        return pair

    async def review_qa_pairs(self, qa_ids: list[str], status: str) -> dict[str, int]:
        """批量审核：draft→approved（可入库）或 →rejected（永不入库）。"""
        if status not in ("approved", "rejected"):
            raise ValueError(f"非法审核状态: {status}")
        pairs = [await self.kb_repo.get_qa_pair(i) for i in qa_ids]
        changed = await self.kb_repo.update_qa_pairs_status(qa_ids, status)
        for doc_id in {p.doc_id for p in pairs if p is not None}:
            await self._touch_qa_doc(doc_id)
        return {"changed": changed, "requested": len(qa_ids)}

    async def delete_qa_pair(self, qa_id: str) -> None:
        pair = await self.kb_repo.get_qa_pair(qa_id)
        if pair is None:
            raise KeyError(f"问答对不存在: {qa_id}")
        await self.kb_repo.delete_qa_pair(qa_id)
        await self._touch_qa_doc(pair.doc_id)

    async def count_qa_pairs(self, doc_ids: list[str], status: str | None = None) -> dict[str, int]:
        """列表页富化：按文档统计问答对数（status 为空则统计全部）。"""
        return await self.kb_repo.count_qa_pairs_by_doc(doc_ids, status=status)

    async def fill_qa_counts(self, docs: list[KbDocument]) -> list[KbDocument]:
        """给响应里的 Q&A 文档填 qa_pair_count / qa_approved_count（两次查询，避免 N+1）。"""
        qa_docs = [d for d in docs if d.doc_type == "qa"]
        if not qa_docs:
            return docs
        ids = [d.doc_id for d in qa_docs]
        total = await self.kb_repo.count_qa_pairs_by_doc(ids)
        approved = await self.kb_repo.count_qa_pairs_by_doc(ids, status="approved")
        for d in qa_docs:
            d.qa_pair_count = total.get(d.doc_id, 0)
            d.qa_approved_count = approved.get(d.doc_id, 0)
        return docs

    async def vectorize_qa_document(self, doc_id: str) -> KbDocument:
        """把 Q&A 文档里 `status='approved'` 的问答对向量化 → ready（每对固定一个 chunk）。

        未审核（draft）与已驳回（rejected）的 pair 永不入库 —— 这是知识库红线的落点。
        全量重建而非增量 patch；先算完向量再删旧 chunk，embedding 失败时旧知识原样保留。
        """
        doc = await self.kb_repo.get_document(doc_id)
        if doc is None:
            raise KeyError(f"文档不存在: {doc_id}")
        if doc.doc_type != "qa":
            raise ValueError(f"文档不是 Q&A 类型（{doc_id}），请用普通向量化接口")
        if doc.status == "ready":
            return doc  # 无内容变动则短路；变动路径都会 _touch_qa_doc 回落 pending

        pairs = [p for p in await self.kb_repo.list_qa_pairs(doc_id) if p.status == "approved"]
        if not pairs:
            raise ValueError(f"没有已审核的问答对（{doc_id}），请先审核再向量化")
        if self.embedder is None or not self.embedder.is_configured():
            raise EmbeddingNotConfiguredError("服务端未配置向量 API key，无法入库知识库")

        base_meta = _base_meta(
            doc_id=doc.doc_id, source_type=doc.source_type, source_id=doc.source_id,
            title=doc.title, author=doc.author, tags=doc.tags, url=doc.url,
        )
        texts = [_qa_chunk_text(p) for p in pairs]
        try:
            vectors = await self._embed_texts_batched(texts)
            if len(vectors) != len(texts):
                raise EmbeddingParseError(
                    f"向量条数不匹配（期望 {len(texts)}，得 {len(vectors)}）"
                )
            rows = [
                KbChunk(
                    chunk_id=uuid.uuid4().hex, doc_id=doc_id, chunk_index=i,
                    text=text, embedding=vec, modality="text", image_url=None,
                    meta=self._qa_chunk_meta(base_meta, pair, i, len(pairs)),
                    created_at=doc.created_at,
                )
                for i, (pair, text, vec) in enumerate(zip(pairs, texts, vectors))
            ]
            await self.kb_repo.delete_chunks_by_doc(doc_id)
            await self.kb_repo.add_chunks(rows)
            await self.kb_repo.set_document_state(doc_id, "ready", len(rows))
        except EmbeddingError:
            await self.kb_repo.set_document_state(doc_id, "failed", doc.chunk_count)
            raise
        return await self._require_doc(doc_id)

    @staticmethod
    def _qa_chunk_meta(base_meta: dict, pair: KbQaPair, index: int, total: int) -> dict[str, Any]:
        """qa_id/question/kind 一并进既有 meta JSON 字段 —— kb_chunks 表零 DDL 变更。"""
        meta = dict(base_meta)
        meta.update(
            kind="qa", qa_id=pair.qa_id, question=pair.question,
            chunk_index=index, chunk_total=total, modality="text",
        )
        return meta

    async def _embed_texts_batched(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), _QA_EMBED_BATCH):
            batch = texts[i : i + _QA_EMBED_BATCH]
            out.extend(await self.embedder.embed([{"text": t} for t in batch]))
        return out

    async def _touch_qa_doc(self, doc_id: str) -> None:
        """Q&A 内容变动后把已就绪文档回落 pending —— 改动必须重新向量化才生效。"""
        doc = await self.kb_repo.get_document(doc_id)
        if doc is not None and doc.doc_type == "qa" and doc.status == "ready":
            await self.kb_repo.set_document_state(doc_id, "pending", doc.chunk_count)

    async def delete_document(self, doc_id: str) -> None:
        await self.kb_repo.delete_document(doc_id)

    async def delete_documents(self, doc_ids: list[str]) -> int:
        deleted = 0
        for did in doc_ids:
            doc = await self.kb_repo.get_document(did)
            if doc is not None:
                await self.kb_repo.delete_document(did)
                deleted += 1
        return deleted

    # ---- 系统产物：一步到位 ----

    def _assert_reviewable(self, source, label: str) -> None:
        """红线门禁：只有审核通过的内容才允许进入知识库。

        这是**状态判断**而非永久拉黑 —— 内容补审核到 approved 后即可正常入库。
        """
        status = getattr(source, "review_status", None)
        if status != "approved":
            raise ReviewGateError(
                f"{label}未审核通过（当前 {status or '未知'}），不得进入知识库；"
                f"请先完成审核并确认 verdict 通过"
            )

    async def vectorize_content(self, content_id: str) -> KbDocument:
        content = await self.content_repo.get(content_id)
        if content is None:
            raise KeyError(f"内容不存在: {content_id}")
        self._assert_reviewable(content, f"内容 {content_id}")
        title = content.title or content_id
        return await self._ingest(
            source_type="content",
            source_id=content_id,
            body=_content_body(content),
            title=title,
            author=content.author_name,
            tags=content.tags,
            url=content.canonical_url,
            image_url=content.cover_local or content.cover_url,
        )

    async def vectorize_analysis(self, analysis_id: str) -> KbDocument:
        analysis = await self.analysis_repo.get(analysis_id)
        if analysis is None:
            raise KeyError(f"分析不存在: {analysis_id}")
        author: str | None = None
        tags: list[str] = []
        url: str | None = None
        source = await self.content_repo.get(analysis.content_id)
        # 分析是模型产物，其可信度依附于源内容的审核结论 —— 源内容未通过则一并拦。
        if source is None:
            raise ReviewGateError(
                f"分析 {analysis_id} 的源内容 {analysis.content_id} 不存在，"
                f"无法确认审核状态，不得进入知识库"
            )
        self._assert_reviewable(source, f"分析 {analysis_id} 的源内容")
        author = source.author_name
        tags = source.tags
        url = source.canonical_url
        title = f"作品分析（{analysis.verdict}）· {analysis.content_id}"
        return await self._ingest(
            source_type="analysis",
            source_id=analysis_id,
            body=analysis.markdown,
            title=title,
            author=author,
            tags=tags,
            url=url,
        )

    # ---- 内部 ----

    async def _ingest(
        self,
        *,
        source_type: str,
        source_id: str | None,
        body: str,
        title: str,
        author: str | None,
        tags: list[str],
        url: str | None,
        image_url: str | None = None,
    ) -> KbDocument:
        if self.embedder is None or not self.embedder.is_configured():
            raise EmbeddingNotConfiguredError("服务端未配置向量 API key，无法入库知识库")
        body = (body or "").strip()
        if not body:
            raise ValueError("无可向量化文本")

        content_hash = _sha256(body)

        # 幂等（仅限有 source_id 的内容/分析）：同源已就绪 → 直接返回；未就绪 → 删除重建。
        # manual（source_id=None）不做 source 去重，靠 hash 在 create_manual_document 阶段去重。
        if source_id:
            existing = await self.kb_repo.get_document_by_source(source_type, source_id)
            if existing and existing.status == "ready":
                return existing
            if existing:
                await self.kb_repo.delete_document(existing.doc_id)

        doc_id = uuid.uuid4().hex
        created_at = _now()
        doc = KbDocument(
            doc_id=doc_id, source_type=source_type, source_id=source_id, title=title,
            author=author, tags=tags, url=url, content_hash=content_hash,
            status="embedding", chunk_count=0, created_at=created_at,
        )
        await self.kb_repo.add_document(doc)

        base_meta = _base_meta(
            doc_id=doc_id, source_type=source_type, source_id=source_id,
            title=title, author=author, tags=tags, url=url,
        )
        try:
            rows, total = await self._embed_rows(
                doc_id, body, base_meta, title, image_url, created_at
            )
            await self.kb_repo.add_chunks(rows)
            await self.kb_repo.set_document_state(doc_id, "ready", len(rows))
        except EmbeddingError as exc:
            await self.kb_repo.set_document_state(doc_id, "failed", 0)
            raise

        return await self._require_doc(doc_id)

    @staticmethod
    def _chunk_meta(base_meta: dict, index: int, total: int) -> dict[str, Any]:
        meta = dict(base_meta)
        meta.update(chunk_index=index, chunk_total=total, modality="text")
        return meta

    async def _embed_rows(
        self, doc_id: str, body: str, base_meta: dict, caption: str,
        image_url: str | None, created_at: str,
    ) -> tuple[list[KbChunk], int]:
        """文本 pieces 向量化；封面图模态追加（失败退纯文本）。返回 (rows, chunk_total)。"""
        pieces = chunk_text(body)
        vectors = await self.embedder.embed([{"text": p} for p in pieces])
        if len(vectors) != len(pieces):
            raise EmbeddingParseError(
                f"向量条数不匹配（期望 {len(pieces)}，得 {len(vectors)}）"
            )
        rows: list[KbChunk] = []
        total = len(pieces)
        for i, (piece, vec) in enumerate(zip(pieces, vectors)):
            meta = self._chunk_meta(base_meta, i, total)
            rows.append(
                KbChunk(
                    chunk_id=uuid.uuid4().hex, doc_id=doc_id, chunk_index=i,
                    text=piece, embedding=vec, modality="text", image_url=None,
                    meta=meta, created_at=created_at,
                )
            )
        if image_url:
            image_chunk = await self._try_embed_image(
                base_meta, doc_id, total, caption, image_url, created_at
            )
            if image_chunk is not None:
                rows.append(image_chunk)
        return rows, total

    async def _try_embed_image(
        self, base_meta: dict, doc_id: str, chunk_total: int, caption: str,
        image_url: str, created_at: str,
    ) -> KbChunk | None:
        try:
            vecs = await self.embedder.embed([{"image": image_url}])
        except EmbeddingError:
            return None  # 图片嵌入失败 → 纯文本退路
        if not vecs:
            return None
        meta = dict(base_meta)
        meta.update(chunk_index=chunk_total, chunk_total=chunk_total + 1, modality="image")
        return KbChunk(
            chunk_id=uuid.uuid4().hex, doc_id=doc_id, chunk_index=chunk_total,
            text=caption or "", embedding=vecs[0], modality="image",
            image_url=image_url, meta=meta, created_at=created_at,
        )

    async def _require_doc(self, doc_id: str) -> KbDocument:
        doc = await self.kb_repo.get_document(doc_id)
        if doc is None:
            raise KeyError(f"文档不存在: {doc_id}")
        return doc

    # ---- 检索 ----

    async def search(
        self,
        query: str,
        top_k: int = 8,
        qa_boost: float | None = None,
        app_visible_only: bool = False,
    ) -> list[dict[str, Any]]:
        """向量检索。Q&A chunk 与普通 markdown chunk 同表同列，天然一起命中。

        `qa_boost`（Q&A 同分优先权重）默认取构造时的配置，**默认 0.0 = 关闭**：
        关闭时排序与切片和引入 Q&A 之前逐字节一致（既有排序断言天然安全）。
        开启后 `score` 仍是原始余弦（可比、可解释），另有 `rank_score` 为加权排序分。

        `app_visible_only`：app 通道下被隐藏内容的 chunk 不参与排序。**这是检索与
        问答的共同入口** —— `/kb/search` 与 `/kb/ask`（经 `qa_agent.KbRetriever`）
        都调到这里，一处过滤覆盖两条路径，没有「问答走另一条路」的漏。
        问答侧不需要额外处理 citations：命中集里没有它，模型若仍引用就对不齐
        检索结果，会走既有的「无有效引用 → 强制拒答并丢弃模型文本」红线。
        """
        query = (query or "").strip()
        if not query:
            return []
        if self.embedder is None or not self.embedder.is_configured():
            raise EmbeddingNotConfiguredError("服务端未配置向量 API key，无法检索知识库")
        boost = self.qa_boost if qa_boost is None else qa_boost
        vecs = await self.embedder.embed([{"text": query}])
        qvec = vecs[0]
        # 走带审核门禁的检索视图：未 approved 的内容/分析产生的 chunk 不参与排序
        chunks = await self.kb_repo.iter_retrievable_chunks(app_visible_only=app_visible_only)
        by_id = {c.chunk_id: c for c in chunks}
        candidates = [(c.chunk_id, c.embedding) for c in chunks if c.embedding]
        limit = max(top_k, 1)

        if boost <= 0:
            ranked = [(float(s), cid, float(s)) for s, cid in _rank_top_k(qvec, candidates, limit)]
        else:
            # 放大候选集再重排，避免截断掉本该被 boost 抬进前 k 的 Q&A chunk
            pool = _rank_top_k(qvec, candidates, max(limit, _QA_BOOST_CANDIDATES))
            weighted = [
                (s, cid, s + boost * (1.0 if by_id[cid].meta.get("kind") == "qa" else 0.0))
                for s, cid in pool
            ]
            weighted.sort(key=lambda t: t[2], reverse=True)
            ranked = weighted[:limit]

        out: list[dict[str, Any]] = []
        for score, chunk_id, rank_score in ranked:
            c = by_id.get(chunk_id)
            if c is None:
                continue
            out.append(
                {
                    "chunk_id": c.chunk_id,
                    "doc_id": c.doc_id,
                    "text": c.text,
                    "score": round(float(score), 4),
                    "rank_score": round(float(rank_score), 4),
                    "modality": c.modality,
                    "image_url": c.image_url,
                    "meta": c.meta,
                }
            )
        return out
