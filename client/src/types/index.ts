export type RunStatus =
  | 'queued'
  | 'running'
  | 'retry_wait'
  | 'success'
  | 'partial'
  | 'blocked'
  | 'failed'
  | 'cancelled'

export type TargetType = 'url' | 'keyword' | 'account' | 'post'

export interface CollectionRun {
  id: string
  platform: string
  target_type: TargetType
  target: string
  max_items: number
  status: RunStatus
  started_at: string | null
  finished_at: string | null
  /** 端到端耗时（ms）。历史行/未结束的任务为 null —— UI 显示「—」，不显示 0。 */
  latency_ms: number | null
  error_code: string | null
  error_category: string | null
  items_found: number
  items_saved: number
  retry_count: number
  rate_limit_signal: number
  tool_version: string | null
}

export interface CollectionEvent {
  run_id: string
  seq: number
  ts: string
  level: string
  category: string | null
  code: string | null
  message: string
}

export interface CollectionRunDetail {
  run: CollectionRun
  events: CollectionEvent[]
}

/** app 提交的采集申请状态。approved 才真正建 run；两个终态都不可回退。 */
export type RequestStatus = 'pending' | 'approved' | 'rejected'

/** app 端提交的待放行采集申请 —— app 只能「申请」，放行权在管理员这里。 */
export interface CollectionRequest {
  id: string
  platform: string
  target_type: TargetType
  target: string
  max_items: number
  status: RequestStatus
  requested_by: string
  run_id: string | null
  decided_at: string | null
  decided_by: string | null
  note: string | null
  created_at: string
}

export interface Engagement {
  likes: string | number | null
  comments: string | number | null
  shares: string | number | null
  collects: string | number | null
}

export interface Content {
  content_id: string
  platform: string
  platform_item_id: string
  content_type: string | null
  canonical_url: string | null
  author_name: string | null
  published_at: string | null
  collected_at: string
  title: string | null
  text: string | null
  cover_url?: string | null
  cover_local?: string | null
  tags?: string[]
  engagement: Engagement
  review_status: string
  active_batch_id?: string | null  // 生效审核批次（null → 最新一批）
  // app 端「删除」= 软隐藏：非空表示已被 app 隐藏（web 端照常可见可恢复）
  app_hidden_at?: string | null
  author_id?: string | null
  media?: unknown[]
  raw_refs?: string[]
}

export type ClaimStatus = 'unverified' | 'supported' | 'contradicted' | 'unclear'
export type ClaimType = 'fact' | 'opinion' | 'prediction' | 'promotion'

export interface Claim {
  claim_id: string
  content_id: string
  text: string
  status: ClaimStatus
  confidence: number | null
  claim_type: ClaimType | null
  meta: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface Evidence {
  evidence_id: string
  claim_id: string
  source_kind: string
  source_ref: string | null
  excerpt: string
  supports: boolean | null
  strength: number
  collected_at: string
}

export interface ReviewDecision {
  decision_id: string
  claim_id: string
  status: ClaimStatus
  rationale: string
  reviewer: string
  evidence_ids: string[]
  created_at: string
}

export interface ClaimSnapshot {
  claim: Claim
  evidence: Evidence[]
  decisions: ReviewDecision[]
}

/** 一次自动审核产出的批次（重审新增批次，旧批次保留，可切回生效）。 */
export interface ReviewBatch {
  batch_id: string
  batch_seq: number
  claim_count: number
  created_at: string | null
  active: boolean
}

export interface ContentReviewDetail {
  content: Content
  claims: ClaimSnapshot[]
  // 审核多版本：batches 为全部历史批次（新→旧），batch_id 为本次返回的批次
  batches: ReviewBatch[]
  batch_id: string | null
}

/** 内容池一行的派生计数（/contents/summary），也是删除影响面的数据源。 */
export interface ContentSummary {
  content_id: string
  analysis_count: number
  latest_analysis_id: string | null
  latest_analysis_at: string | null
  report_count: number
  claim_count: number
  review_batch_count: number
  kb_doc_count: number
}

export interface Report {
  report_id: string
  title: string
  content_ids: string[]
  payload: Record<string, unknown>
  markdown: string
  created_at: string
  schema_version: string
}

// ---- 作品分析（阶段 2）----

export type AnalysisVerdict = 'viral' | 'flat' | 'uncertain'

export interface AnalysisReason {
  factor: string
  evidence: string
  confidence: number
}

export interface AnalysisComparison {
  baseline_content_ids: string[]
  baseline_count: number
  differentiators: string[]
  shared_patterns: string[]
}

export interface AnalysisPayloadData {
  analysis_id: string
  content_id: string
  verdict: AnalysisVerdict
  summary: string
  topic: string
  viral_reasons: AnalysisReason[]
  flat_reasons: AnalysisReason[]
  hooks: string[]
  audience: string[]
  comparison: AnalysisComparison
  suggestions: string[]
  confidence: number
  limitations: string[]
  schema_version: string
  focus?: string  // 重新分析时用户注入的补充视角（"" = 标准分析）
}

export interface Analysis {
  analysis_id: string
  content_id: string
  verdict: AnalysisVerdict
  payload: AnalysisPayloadData
  markdown: string
  compared_with: string[]
  focus: string
  created_at: string
  schema_version: string
  in_kb: boolean  // 该版本是否已入库（列表里标「✓已入库」、禁用重复入库）
}

// ---- 知识库（阶段 3）----

export type KbSourceType = 'analysis' | 'manual' | 'content'
export type KbStatus = 'pending' | 'embedding' | 'ready' | 'failed'
export type KbDocType = 'general' | 'qa'

export interface KbDocument {
  doc_id: string
  source_type: KbSourceType
  source_id: string | null
  doc_type: KbDocType
  title: string
  author: string | null
  tags: string[]
  url: string | null
  content_hash: string
  raw_text?: string
  markdown?: string
  status: KbStatus
  chunk_count: number
  qa_pair_count: number
  qa_approved_count: number
  created_at: string
}

export interface KbVectorizeResult {
  doc_id: string
  ok: boolean
  status: string
  title: string | null
  error: string | null
}

export interface KbFileText {
  filename: string
  text: string
}

export interface KbSearchHit {
  chunk_id: string
  doc_id: string
  text: string
  score: number
  rank_score: number
  modality: string
  image_url: string | null
  meta: Record<string, unknown>
}

// ---- Q&A 知识（蒸馏 / 手动录入 / 审核 / 问答）----

export type KbQaStatus = 'draft' | 'approved' | 'rejected'

export interface KbQaPair {
  qa_id: string
  doc_id: string
  qa_index: number
  question: string
  answer: string
  dimensions: Record<string, unknown>
  evidence: Array<{ source_kind: string; ref: string; excerpt: string }>
  tags: string[]
  source_type: string
  source_id: string | null
  source_url: string | null
  source_author: string | null
  status: KbQaStatus
  created_at: string
  updated_at: string
}

export interface KbQaPairInput {
  question: string
  answer: string
  dimensions?: Record<string, unknown>
  evidence?: Array<{ source_kind: string; ref: string; excerpt: string }>
  tags?: string[]
}

export interface DistillResult {
  doc: KbDocument
  pairs: KbQaPair[]
  reused: boolean
  replaced_drafts: number
  limitations: string[]
  dropped_refs: number
}

export interface KbQaReviewResult {
  changed: number
  requested: number
}

export interface KbCitation {
  chunk_id: string
  doc_id: string
  title: string
  text: string
  score: number
  verified: boolean
  qa_id: string | null
  question: string | null
}

export interface KbAnswer {
  answered: boolean
  answer: string
  citations: KbCitation[]
  top_score: number
  retrieval_count: number
  limitations: string[]
  reason: string
}

export interface HealthResponse {
  ok: boolean
  logged_in: boolean
  username: string | null
  profile: string | null
  detail: string | null
  breaker_open: string[]
  cached?: boolean    // 命中服务端 TTL 缓存：ok/detail 来自 checked_at 那一刻
  checked_at?: string // 真实探测时刻（ISO8601 UTC）
}

// ---- 浏览器连接（Browser Bridge）----

export interface BrowserTab {
  index: number | null
  page: string | null
  url: string | null
  title: string | null
  active: boolean | null
}

export interface BrowserStatus {
  session: string
  reachable: boolean
  tabs: BrowserTab[]
  xhs_open: boolean
  detail: string | null
}

export type BrowserAction = 'connected' | 'reused' | 'opened' | 'failed'

export interface BrowserConnectResult {
  session: string
  action: BrowserAction
  xhs_open: boolean
  url: string | null
  detail: string | null
}
