"""API 请求/响应 schema。响应大多直接复用 domain 模型（已 pydantic）。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.domain.enums import ClaimStatus, ReviewStatus, TargetType
from app.domain.models import (
    Claim,
    CollectionEvent,
    CollectionRun,
    Content,
    Evidence,
    KbDocument,
    Report,
    ReviewDecision,
)


class CreateCollectionRunRequest(BaseModel):
    platform: str = "xhs"
    target_type: TargetType
    target: str = Field(min_length=1, max_length=2000)
    max_items: int = Field(default=20, ge=1, le=100)


class CollectionRunDetail(BaseModel):
    run: CollectionRun
    events: list[CollectionEvent] = Field(default_factory=list)


# ---- app 采集申请（提交是纯写库；放行才真正建 run）----

class CreateCollectionRequestRequest(BaseModel):
    """app 提交采集意图。只收 keyword —— 检索式采集是 app 的唯一形态。"""
    target: str = Field(min_length=1, max_length=200)
    max_items: int = Field(default=10, ge=1, le=100)
    platform: str = "xhs"


class DecideCollectionRequestRequest(BaseModel):
    """管理员决策。note 仅对 reject 有意义（驳回理由），approve 时可省。"""
    action: Literal["approve", "reject"]
    note: str = Field(default="", max_length=500)


class HealthResponse(BaseModel):
    ok: bool
    logged_in: bool = False
    username: str | None = None
    profile: str | None = None
    detail: str | None = None
    breaker_open: list[str] = Field(default_factory=list)
    lc: str = ""            # LangChain 编排后端：langchain-core | fallback（shim）
    lc_tracing: bool = False  # 恒为 False：lc.py 强制关闭 langsmith，防采集内容外发
    # 诚实标注缓存：cached=True 时 ok/detail 来自 checked_at 那一刻，不是本次请求
    cached: bool = False
    checked_at: str = ""    # 真实探测时刻（ISO8601 UTC）


# ---- Milestone 2 审核 / 报告 ----

class SubmitReviewResponse(BaseModel):
    content_id: str
    review_status: ReviewStatus
    claim_count: int = 0


class ContentSummary(BaseModel):
    """内容池一行的派生计数（GET /contents/summary），替代前端逐条探测的 N+1。

    也是删除确认弹窗的数据源：影响面直接取这里的计数，不再额外发请求。
    """
    content_id: str
    analysis_count: int = 0
    latest_analysis_id: str | None = None
    latest_analysis_at: str | None = None
    report_count: int = 0
    claim_count: int = 0
    review_batch_count: int = 0
    kb_doc_count: int = 0
    app_hidden_at: str | None = None  # 非空 = app 端已隐藏（web 看板据此打标记）


class ClaimSnapshot(BaseModel):
    claim: Claim
    evidence: list[Evidence] = Field(default_factory=list)
    decisions: list[ReviewDecision] = Field(default_factory=list)


class ReviewBatch(BaseModel):
    """一次自动审核产出的批次摘要（重审新增批次，旧批次保留）。"""
    batch_id: str
    batch_seq: int
    claim_count: int = 0
    created_at: str | None = None
    active: bool = False


class ContentReviewDetail(BaseModel):
    content: Content
    claims: list[ClaimSnapshot] = Field(default_factory=list)
    # 审核多版本：batches 为全部历史批次（新→旧），batch_id 为本次返回的批次
    batches: list[ReviewBatch] = Field(default_factory=list)
    batch_id: str | None = None


class DecisionRequest(BaseModel):
    status: ClaimStatus
    rationale: str = Field(default="", max_length=500)
    evidence_ids: list[str] = Field(default_factory=list)


class AnalyzeRequest(BaseModel):
    """作品分析入参。focus = 重新分析时的补充关注点（只补充观察侧重，不改判定口径）。"""
    focus: str = Field(default="", max_length=500)


class ReportCreateRequest(BaseModel):
    content_ids: list[str] = Field(min_length=1, max_length=100)
    title: str = Field(default="审核报告", max_length=200)


# ---- 知识库（阶段 3）----

class KbManualDocumentRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=100_000)
    tags: list[str] = Field(default_factory=list)
    url: str | None = Field(default=None, max_length=2000)


class KbFileText(BaseModel):
    filename: str
    text: str  # 上传解析出的纯文本（回填 textarea，未落库）


class KbDocIdsRequest(BaseModel):
    doc_ids: list[str] = Field(min_length=1, max_length=500)


class KbVectorizeResult(BaseModel):
    doc_id: str
    ok: bool
    status: str  # ready | failed | pending | embedding
    title: str | None = None
    error: str | None = None


class KbSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=8, ge=1, le=50)
    qa_boost: float | None = Field(default=None, ge=0, le=1)  # 不传则用服务端配置


class KbSearchHit(BaseModel):
    chunk_id: str
    doc_id: str
    text: str
    score: float       # 原始余弦相似度（可比、可解释）
    rank_score: float = 0.0  # 加权排序分；qa_boost=0 时等于 score
    modality: str = "text"
    image_url: str | None = None
    meta: dict = Field(default_factory=dict)


# ---- Q&A 知识（蒸馏 / 手动录入 / 审核 / 问答）----

class KbQaPairInput(BaseModel):
    """手动录入的单条问答（人写即终态，不走 LLM 清洗）。"""
    question: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=4000)
    dimensions: dict = Field(default_factory=dict)
    evidence: list[dict] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class KbQaManualRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    pairs: list[KbQaPairInput] = Field(min_length=1, max_length=50)
    tags: list[str] = Field(default_factory=list)
    url: str | None = Field(default=None, max_length=2000)


class KbQaDistillTextRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    text: str = Field(min_length=1, max_length=100_000)
    tags: list[str] = Field(default_factory=list)


class KbQaIdsRequest(BaseModel):
    qa_ids: list[str] = Field(min_length=1, max_length=500)


class KbQaUpdateRequest(BaseModel):
    """编辑问答对；只传需要改的字段。已审核内容被改会自动回落草稿。"""
    question: str | None = Field(default=None, max_length=500)
    answer: str | None = Field(default=None, max_length=4000)
    dimensions: dict | None = None
    tags: list[str] | None = None


class KbQaReviewResult(BaseModel):
    changed: int      # 实际变动的行数
    requested: int    # 请求里的 id 数（不等即说明有 id 不存在）


class KbAskRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=50)
    history: list[dict[str, str]] = Field(default_factory=list, max_length=6)


class KbCitation(BaseModel):
    chunk_id: str
    doc_id: str
    title: str = ""
    text: str = ""
    score: float = 0.0
    verified: bool = False          # 引用原文能在命中文本里逐字对齐
    qa_id: str | None = None
    question: str | None = None


class KbAnswer(BaseModel):
    answered: bool
    answer: str = ""
    citations: list[KbCitation] = Field(default_factory=list)
    top_score: float = 0.0          # 供阈值校准：本次命中的最高余弦
    retrieval_count: int = 0
    limitations: list[str] = Field(default_factory=list)
    reason: str = ""                # 拒答原因：empty_retrieval | below_threshold | no_valid_citation


# ---- 浏览器连接（Browser Bridge）----

class BrowserTab(BaseModel):
    index: int | None = None
    page: str | None = None
    url: str | None = None
    title: str | None = None
    active: bool | None = None


class BrowserStatus(BaseModel):
    session: str
    reachable: bool = False
    tabs: list[BrowserTab] = Field(default_factory=list)
    xhs_open: bool = False
    detail: str | None = None


class BrowserConnectResult(BaseModel):
    session: str
    action: str  # connected | reused | opened | failed
    xhs_open: bool = False
    url: str | None = None
    detail: str | None = None
