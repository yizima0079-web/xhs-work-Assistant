"""Q&A 蒸馏服务：爆款作品/分析 → 标准「问题 + 答案」知识条目（草稿）。

三个入口，产物结构一致（doc_type='qa' 的容器文档 + 若干 status='draft' 的问答对）：
- `distill_from_analysis`：素材 = 作品真实数据 + 同话题基线数据 + 归因分析报告；
- `distill_from_content`：素材 = 作品真实数据（+ 最近一次分析，若有）；
- `distill_text`：素材 = 人工粘进来的文本，先走既有 clean 整理成 markdown 再蒸馏。

防幻觉靠三件事叠加，缺一不可：
1) prompt 铁律（见 prompt_templates.build_viral_qa_messages）；
2) `sanitize_qa_payload` 用**服务端登记的来源表**给 evidence.ref 求交，越界丢弃；
3) 人工闸门 draft→approved，未审核的 pair 永远不进 kb_chunks（本服务只产草稿，不向量化）。

幂等：同 (source_type, source_id) 已有 Q&A 文档时，默认直接复用（reused=True）；
`force=True` 才重新蒸馏，且**只删旧 draft**，approved 永不自动删除（人工成果不会被机器覆盖）。
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from app.adapters.llm import ModelAdapter, ModelNotConfiguredError, ModelParseError
from app.domain.models import Content, KbDocument, KbQaPair
from app.repositories.base import AnalysisRepository, ContentRepository, KnowledgeBaseRepository
from app.schemas.qa import (
    MAX_PAIRS,
    DistillResult,
    QaDistillPayload,
    QaPairDraft,
    QaSchemaError,
    sanitize_qa_payload,
)
from app.services.analysis import topic_seed
from app.services.markdown_cleaner import clean as clean_markdown
from app.services.prompt_templates import analysis_digest, build_viral_qa_messages

_MAX_BASELINES = 6
_BASELINE_DIGEST_MAX = 700
_ANALYSIS_MATERIAL_MAX = 9000

_LABEL_ANALYSIS = "该作品的爆款/平淡归因分析报告（含判定依据与对比结论）"
_LABEL_CONTENT = "作品原始数据（标题/正文/标签/真实互动数字）"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def render_qa_markdown(title: str, pairs: list[KbQaPair]) -> str:
    """把问答对渲染成容器文档的 markdown 正文（前端预览/溯源用）。

    这条 markdown **不参与向量化** —— Q&A 文档的向量来自每个 approved pair 单独一个 chunk，
    避免 chunk_text 按字符切分把问题与答案拆散。
    """
    lines = [f"# {title}", ""]
    for i, p in enumerate(pairs, 1):
        lines.append(f"## Q{i}：{p.question}")
        lines.append("")
        lines.append(p.answer)
        lines.append("")
        dims = p.dimensions or {}
        for key, label in (
            ("technique", "表现手法"), ("persona", "IP 人设"),
            ("hook", "开头钩子"), ("structure", "结构节奏"),
        ):
            if dims.get(key):
                lines.append(f"- **{label}**：{dims[key]}")
        for key, label in (("transfer", "迁移建议"), ("transfer_risk", "失败风险")):
            items = dims.get(key) or []
            if items:
                lines.append(f"- **{label}**：")
                lines.extend(f"  - {item}" for item in items)
        if p.tags:
            lines.append(f"- **标签**：{' '.join(p.tags)}")
        lines.append("")
    return "\n".join(lines).strip()


class QaDistillerService:
    """蒸馏编排：素材 → prompt → LLM → 净化 → 容器文档 + 草稿问答对。"""

    def __init__(
        self,
        kb_repo: KnowledgeBaseRepository,
        content_repo: ContentRepository,
        analysis_repo: AnalysisRepository,
        llm: ModelAdapter | None,
        max_pairs: int = MAX_PAIRS,
    ):
        self.kb_repo = kb_repo
        self.content_repo = content_repo
        self.analysis_repo = analysis_repo
        self.llm = llm
        self.max_pairs = max_pairs

    # ---- 三个入口 ----

    async def distill_from_analysis(self, analysis_id: str, *, force: bool = False) -> DistillResult:
        analysis = await self.analysis_repo.get(analysis_id)
        if analysis is None:
            raise KeyError(f"分析不存在: {analysis_id}")
        content = await self.content_repo.get(analysis.content_id)
        if content is None:
            raise KeyError(f"内容不存在: {analysis.content_id}")

        baselines = await self._baselines(content, analysis.compared_with)
        refs: list[dict[str, str]] = [{"ref": analysis_id, "kind": "analysis", "label": _LABEL_ANALYSIS}]
        refs += [{"ref": content.content_id, "kind": "content", "label": _LABEL_CONTENT}]
        refs += [{"ref": b.content_id, "kind": "content", "label": _LABEL_CONTENT} for b in baselines]

        blocks = [f"【目标作品数据】\n{analysis_digest(content)}"]
        if baselines:
            blocks.append(
                "【同话题基线作品数据】\n"
                + "\n\n".join(analysis_digest(b)[:_BASELINE_DIGEST_MAX] for b in baselines)
            )
        blocks.append(f"【归因分析报告】\n{(analysis.markdown or '')[: _ANALYSIS_MATERIAL_MAX]}")
        material = "\n\n".join(blocks)

        return await self._distill(
            source_type="analysis",
            source_id=analysis_id,
            title=f"爆款拆解 Q&A · {content.title or content.content_id}",
            material=material,
            refs=refs,
            author=content.author_name,
            tags=content.tags,
            url=content.canonical_url,
            force=force,
        )

    async def distill_from_content(self, content_id: str, *, force: bool = False) -> DistillResult:
        content = await self.content_repo.get(content_id)
        if content is None:
            raise KeyError(f"内容不存在: {content_id}")

        analyses = await self.analysis_repo.list_by_content(content_id, limit=1)
        refs: list[dict[str, str]] = [{"ref": content_id, "kind": "content", "label": _LABEL_CONTENT}]
        blocks = [f"【作品数据】\n{analysis_digest(content)}"]
        if analyses:
            refs.append({"ref": analyses[0].analysis_id, "kind": "analysis", "label": _LABEL_ANALYSIS})
            blocks.append(f"【归因分析报告】\n{(analyses[0].markdown or '')[: _ANALYSIS_MATERIAL_MAX]}")

        return await self._distill(
            source_type="content",
            source_id=content_id,
            title=f"爆款拆解 Q&A · {content.title or content_id}",
            material="\n\n".join(blocks),
            refs=refs,
            author=content.author_name,
            tags=content.tags,
            url=content.canonical_url,
            force=force,
        )

    async def distill_text(
        self, title: str, text: str, tags: list[str] | None = None
    ) -> DistillResult:
        """人工粘入的文本：先走既有 clean 整理成 markdown，再蒸馏。无结构化来源可引用。"""
        title = (title or "").strip()
        raw = (text or "").strip()
        if not title:
            raise ValueError("蒸馏标题不能为空")
        if not raw:
            raise ValueError("蒸馏素材不能为空")

        markdown = await clean_markdown(raw, self.llm)  # 无 LLM → ModelNotConfiguredError(503)
        return await self._distill(
            source_type="manual",
            source_id=None,
            title=f"爆款拆解 Q&A · {title}",
            material=markdown,
            refs=[],
            author=None,
            tags=tags or [],
            url=None,
            force=False,
        )

    # ---- 内部 ----

    async def _baselines(self, content: Content, compared_with: list[str]) -> list[Content]:
        """同话题基线作品：取分析时真实参与过对比的 content（服务端记录，非模型自报）。"""
        out: list[Content] = []
        for cid in compared_with[:_MAX_BASELINES]:
            if cid == content.content_id:
                continue
            item = await self.content_repo.get(cid)
            if item is not None:
                out.append(item)
        return out

    async def _distill(
        self,
        *,
        source_type: str,
        source_id: str | None,
        title: str,
        material: str,
        refs: list[dict[str, str]],
        author: str | None,
        tags: list[str],
        url: str | None,
        force: bool,
    ) -> DistillResult:
        if self.llm is None or not self.llm.is_configured():
            raise ModelNotConfiguredError("服务端未配置模型 API key，无法蒸馏 Q&A 知识")

        existing = (
            await self.kb_repo.get_document_by_source(source_type, source_id)
            if source_id else None
        )
        if existing is not None and existing.doc_type != "qa":
            # 同源已有普通 markdown 文档：不抢占，另立 Q&A 文档（source 幂等只对同类型生效）
            existing = None

        if existing is not None and not force:
            pairs = await self.kb_repo.list_qa_pairs(existing.doc_id)
            return DistillResult(doc=existing, pairs=pairs, reused=True)

        allowed = {r["ref"]: r["kind"] for r in refs}
        raw = await self.llm.chat_json(
            build_viral_qa_messages(
                title=title,
                material=material,
                allowed_refs=refs,
                max_pairs=self.max_pairs,
                extra_instruction="本次素材没有可引用的结构化来源，evidence 一律给空数组。"
                if not refs else "",
            )
        )
        try:
            payload = sanitize_qa_payload(
                raw, title=title, allowed_refs=allowed, max_pairs=self.max_pairs
            )
        except QaSchemaError as exc:
            raise ModelParseError(f"蒸馏结构不可用: {exc}") from exc

        return await self._persist(
            existing=existing, payload=payload, source_type=source_type, source_id=source_id,
            title=title, material=material, author=author, tags=tags, url=url, force=force,
        )

    async def _persist(
        self,
        *,
        existing: KbDocument | None,
        payload: QaDistillPayload,
        source_type: str,
        source_id: str | None,
        title: str,
        material: str,
        author: str | None,
        tags: list[str],
        url: str | None,
        force: bool,
    ) -> DistillResult:
        now = _now()
        replaced = 0
        if existing is None:
            doc = KbDocument(
                doc_id=uuid.uuid4().hex, source_type=source_type, source_id=source_id,
                doc_type="qa", title=title, author=author, tags=tags, url=url,
                content_hash="", raw_text=material, markdown="",
                status="pending", chunk_count=0, created_at=now,
            )
            await self.kb_repo.add_document(doc)
            start_index = 0
        else:
            doc = existing
            replaced = await self.kb_repo.delete_qa_pairs_by_doc(doc.doc_id, status="draft")
            kept = await self.kb_repo.list_qa_pairs(doc.doc_id)
            start_index = max((p.qa_index for p in kept), default=-1) + 1
            # 新草稿未入库：文档回落 pending，等人工审核后重新向量化（approved chunk 仍在，幂等重建）
            await self.kb_repo.set_document_state(doc.doc_id, "pending", doc.chunk_count)

        pairs = [
            KbQaPair(
                qa_id=uuid.uuid4().hex, doc_id=doc.doc_id, qa_index=start_index + i,
                question=draft.question, answer=draft.answer,
                dimensions=draft.dimensions,
                evidence=[e.model_dump() for e in draft.evidence],
                tags=draft.tags or list(tags),
                source_type="distilled", source_id=source_id,
                source_url=url, source_author=author,
                status="draft", created_at=now, updated_at=now,
            )
            for i, draft in enumerate(payload.qa_pairs)
        ]
        await self.kb_repo.add_qa_pairs(pairs)

        all_pairs = await self.kb_repo.list_qa_pairs(doc.doc_id)
        markdown = render_qa_markdown(title, all_pairs)
        await self.kb_repo.update_document_body(
            doc.doc_id, title, markdown, material, _sha256(markdown)
        )

        fresh = await self.kb_repo.get_document(doc.doc_id)
        return DistillResult(
            doc=fresh or doc, pairs=pairs, reused=False, replaced_drafts=replaced,
            limitations=payload.limitations, dropped_refs=payload.dropped_refs,
        )

    # ---- 手动录入（不走 LLM，无 key 也能用；因此也不需要 sanitize 之外的任何清洗）----

    async def create_manual_qa_document(
        self, title: str, pairs: list[QaPairDraft], tags: list[str] | None = None,
        url: str | None = None,
    ) -> DistillResult:
        """人工结构化撰写：question/answer 即终态，红线由 draft→approved 人工闸门满足。"""
        title = (title or "").strip()
        if not title:
            raise ValueError("文档标题不能为空")
        if not pairs:
            raise ValueError("至少需要一条问答对")

        now = _now()
        doc = KbDocument(
            doc_id=uuid.uuid4().hex, source_type="manual", source_id=None, doc_type="qa",
            title=title, author=None, tags=tags or [], url=url,
            content_hash="", raw_text="", markdown="", status="pending", chunk_count=0,
            created_at=now,
        )
        await self.kb_repo.add_document(doc)

        rows = [
            KbQaPair(
                qa_id=uuid.uuid4().hex, doc_id=doc.doc_id, qa_index=i,
                question=d.question, answer=d.answer, dimensions=d.dimensions,
                evidence=[e.model_dump() for e in d.evidence],
                tags=d.tags or list(tags or []),
                source_type="manual", source_id=None, source_url=url, source_author=None,
                status="draft", created_at=now, updated_at=now,
            )
            for i, d in enumerate(pairs)
        ]
        await self.kb_repo.add_qa_pairs(rows)
        markdown = render_qa_markdown(title, rows)
        await self.kb_repo.update_document_body(doc.doc_id, title, markdown, "", _sha256(markdown))

        fresh = await self.kb_repo.get_document(doc.doc_id)
        return DistillResult(doc=fresh or doc, pairs=rows)
