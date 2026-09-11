"""领域数据模型（pydantic，便于序列化进 API / 存储 JSON 字段）。

统一内容模型对齐手册 §5.2；互动数据是快照，必须带 collected_at。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import (
    ClaimStatus,
    ClaimType,
    ContentType,
    EvidenceKind,
    ErrorCategory,
    MediaPolicy,
    RequestStatus,
    ReviewStatus,
    RunStatus,
    TargetType,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class CollectRequest(BaseModel):
    """**适配器层**的一次采集调用参数（传给 CollectorAdapter.collect），不落库。

    别和下面的 CollectionRequest 混：那个是 app 提交、待管理员放行的**申请单**（落
    collection_requests 表）。本类的 request_id 是把它关联回申请单的字段。
    """
    platform: str = "xhs"
    target_type: TargetType
    target: str
    max_items: int = Field(default=20, ge=1, le=100)
    media_policy: MediaPolicy = MediaPolicy.METADATA_ONLY
    request_id: str | None = None


class CollectionRequest(BaseModel):
    """app 端提交的**待放行采集申请**（落 collection_requests 表）。

    与上面的 CollectRequest 只差一个字母但语义完全不同：那个是「已经决定要采集」的调用
    参数，这个是「想做，但等管理员点头」的意图。放行后由路由建 CollectionRun 并回填 run_id。
    """
    id: str
    platform: str = "xhs"
    target_type: TargetType = TargetType.KEYWORD
    target: str
    max_items: int = 10
    status: RequestStatus = RequestStatus.PENDING
    requested_by: str = "app"  # app | web
    run_id: str | None = None
    decided_at: str | None = None
    decided_by: str | None = None
    note: str | None = None
    created_at: str = Field(default_factory=_now_iso)


class Engagement(BaseModel):
    likes: str | int | None = None
    comments: str | int | None = None
    shares: str | int | None = None
    collects: str | int | None = None


class MediaRef(BaseModel):
    kind: str | None = None  # image | video
    url: str | None = None
    sha256: str | None = None
    mime: str | None = None
    bytes: int | None = None


class Content(BaseModel):
    content_id: str  # platform:platform_item_id
    platform: str = "xhs"
    platform_item_id: str
    content_type: ContentType | None = None
    canonical_url: str | None = None
    author_id: str | None = None
    author_name: str | None = None
    published_at: str | None = None
    collected_at: str = Field(default_factory=_now_iso)
    title: str | None = None
    text: str | None = None
    cover_url: str | None = None  # 首图封面（note 详情 __INITIAL_STATE__）
    cover_local: str | None = None  # 本地封面 /media/xx（下载成功则优先于 cover_url 展示）
    tags: list[str] = Field(default_factory=list)
    media: list[MediaRef] = Field(default_factory=list)
    engagement: Engagement = Field(default_factory=Engagement)
    raw_refs: list[str] = Field(default_factory=list)  # raw_assets.id 引用
    review_status: ReviewStatus = ReviewStatus.PENDING
    active_batch_id: str | None = None  # 生效审核批次；None → 取 batch_seq 最大的一批
    # 非空 = 该时刻起对 app 通道隐藏。web（管理员 Cookie）通道恒可见，可恢复。
    # app 通道下这个字段恒为 None（隐藏的行根本不会返回），不需要按通道裁剪响应形状。
    app_hidden_at: str | None = None


class CollectionEvent(BaseModel):
    run_id: str
    seq: int = 0
    ts: str = Field(default_factory=_now_iso)
    level: str = "info"  # info | warn | error
    category: ErrorCategory | None = None
    code: str | None = None
    message: str = ""


class CollectionRun(BaseModel):
    id: str
    request_id: str | None = None
    platform: str = "xhs"
    target_type: TargetType
    target: str
    max_items: int = 20
    status: RunStatus = RunStatus.QUEUED
    started_at: str | None = None
    finished_at: str | None = None
    # 端到端耗时（ms）。由仓储层算好落库，缺失为 None（不写 0 假装"瞬间完成"）。
    latency_ms: int | None = None
    error_code: str | None = None
    error_category: ErrorCategory | None = None
    items_found: int = 0
    items_saved: int = 0
    retry_count: int = 0
    rate_limit_signal: int = 0
    tool_version: str | None = None


class RawAsset(BaseModel):
    id: str
    run_id: str | None = None
    content_id: str | None = None
    kind: str | None = None
    mime: str = "application/json"
    sha256: str | None = None
    bytes: int = 0
    storage_path: str | None = None
    collected_at: str = Field(default_factory=_now_iso)


class CollectResult(BaseModel):
    """CollectorAdapter.collect 的返回（手册 §4.1）。"""
    run_id: str
    status: RunStatus = RunStatus.SUCCESS
    items: list[Content] = Field(default_factory=list)
    items_found: int = 0
    items_saved: int = 0
    error_category: ErrorCategory | None = None
    error_code: str | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)


class HealthResult(BaseModel):
    ok: bool
    logged_in: bool = False
    username: str | None = None
    profile: str | None = None
    detail: str | None = None


class CapabilityReport(BaseModel):
    platform: str
    commands: list[str]
    target_types: list[TargetType]


class Claim(BaseModel):
    claim_id: str
    content_id: str
    text: str = Field(min_length=1)
    status: ClaimStatus = ClaimStatus.UNVERIFIED
    confidence: float | None = Field(default=None, ge=0, le=1)
    claim_type: ClaimType | None = None
    meta: dict[str, Any] = Field(default_factory=dict)  # entities/time_scope 等抽取附注
    # 审核版本化：每次重审新增一个批次，旧批次原样保留（"" = 迁移前的历史行，视作第 1 版）
    batch_id: str = ""
    batch_seq: int = 1
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class Evidence(BaseModel):
    evidence_id: str
    claim_id: str
    source_kind: EvidenceKind
    source_ref: str | None = None
    excerpt: str = ""
    supports: bool | None = None
    strength: float = Field(default=1.0, ge=0, le=1)
    collected_at: str = Field(default_factory=_now_iso)


class ReviewDecision(BaseModel):
    decision_id: str
    claim_id: str
    status: ClaimStatus
    rationale: str = ""
    reviewer: str = "rule-engine"
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now_iso)


class Report(BaseModel):
    report_id: str
    title: str = "审核报告"
    content_ids: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    markdown: str = ""
    created_at: str = Field(default_factory=_now_iso)
    schema_version: str = "1.0"


class Analysis(BaseModel):
    """单条作品分析（爆款/平淡归因，可选同话题横向对比）。JSON+Markdown 双格式落库。"""
    analysis_id: str
    content_id: str
    verdict: str = "uncertain"  # viral | flat | uncertain
    payload: dict[str, Any] = Field(default_factory=dict)
    markdown: str = ""
    compared_with: list[str] = Field(default_factory=list)  # 真实参与对比的基线 content_id
    focus: str = ""  # 用户补充视角（重新分析时注入）；只影响观察侧重，不改判定口径
    created_at: str = Field(default_factory=_now_iso)
    schema_version: str = "1.0"
    # 响应期字段：由路由按 kb_documents 填充，不落 analyses 表
    in_kb: bool = False  # 该版本是否已作为 ready 文档入库（列表里标「✓已入库」）


class KbDocument(BaseModel):
    """知识库文档（向量化的来源单元，dedup 靠 content_hash）。

    两阶段入库：manual 文档先清洗存 raw_text+markdown、status=pending（未入库、可预览），
    逐个/批量向量化后才置 ready。content/analysis 系统产物走一步到位 ready。
    status: pending（已清洗未向量化）| embedding（向量化中）| ready（已入库可检索）| failed
    """
    doc_id: str
    source_type: str = "manual"  # analysis | manual | content
    source_id: str | None = None  # content_id / analysis_id；manual 置空（靠 hash 去重）
    doc_type: str = "general"  # general（markdown 切片）| qa（Q&A 对，见 KbQaPair）
    title: str = ""
    author: str | None = None
    tags: list[str] = Field(default_factory=list)
    url: str | None = None
    content_hash: str = ""
    raw_text: str = ""  # 入库前原始文本（预览/溯源）
    markdown: str = ""  # 清洗后的标题分级 markdown（向量化真正入口，未清洗则空）
    status: str = "pending"  # pending | embedding | ready | failed
    chunk_count: int = 0
    created_at: str = Field(default_factory=_now_iso)
    # 响应期字段：由路由按 kb_qa_pairs 填充，不落 kb_documents 表
    qa_pair_count: int = 0      # 全部问答对数（含草稿/驳回）
    qa_approved_count: int = 0  # 已审核通过、可入库的对数


class KbQaPair(BaseModel):
    """标准问答知识条目（doc_type='qa' 文档下的知识原子）。

    一条 = 一个问题 + 一个答案 + 拆解维度 + 证据 + 溯源。审核状态 draft→approved 是入库闸门：
    只有 approved 会被向量化进 kb_chunks（每个 pair 一个 chunk，问题与答案同块不切分）。
    dimensions 键：technique（表现手法）/ persona（IP 人设）/ hook（开头钩子）/
    structure（结构节奏）/ transfer（list[str]，换方式的迁移建议）/ transfer_risk（list[str]，失败风险）。
    """
    qa_id: str
    doc_id: str
    qa_index: int = 0
    question: str
    answer: str = ""
    dimensions: dict[str, Any] = Field(default_factory=dict)
    evidence: list[dict[str, Any]] = Field(default_factory=list)  # [{source_kind, ref, excerpt}]
    tags: list[str] = Field(default_factory=list)
    source_type: str = "manual"  # distilled | manual
    source_id: str | None = None
    source_url: str | None = None
    source_author: str | None = None
    status: str = "draft"  # draft | approved | rejected
    created_at: str = Field(default_factory=_now_iso)
    updated_at: str = Field(default_factory=_now_iso)


class KbChunk(BaseModel):
    """知识库分块（doc 内文本/图片模态切片，带向量与统一 meta）。"""
    chunk_id: str
    doc_id: str
    chunk_index: int = 0
    text: str = ""
    embedding: list[float] = Field(default_factory=list)
    modality: str = "text"  # text | image
    image_url: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)  # 统一"预设文档形式"见 services.knowledge_base
    created_at: str = Field(default_factory=_now_iso)
