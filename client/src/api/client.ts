import type {
  Analysis,
  CollectionRequest,
  CollectionRun,
  CollectionRunDetail,
  Content,
  ContentReviewDetail,
  ContentSummary,
  ReviewBatch,
  ClaimStatus,
  Report,
  HealthResponse,
  BrowserStatus,
  BrowserConnectResult,
  TargetType,
  KbDocument,
  KbSearchHit,
  KbVectorizeResult,
  KbFileText,
  KbQaPair,
  KbQaPairInput,
  DistillResult,
  KbQaReviewResult,
  KbAnswer,
} from '../types'
import { writeClientLog } from '../lib/logger'

const BASE = '/api/v1'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const requestId = crypto.randomUUID()
  const started = performance.now()
  writeClientLog('info', 'api.request', path, requestId)
  let res: Response
  const headers: Record<string, string> = { 'X-Request-ID': requestId, ...(init?.headers as Record<string, string>) }
  // FormData（文件上传）不能手动设 Content-Type，需让浏览器带 multipart boundary
  const isForm = typeof FormData !== 'undefined' && init?.body instanceof FormData
  if (!isForm && !headers['Content-Type']) headers['Content-Type'] = 'application/json'
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      credentials: 'include',
      headers,
    })
  } catch (error) {
    writeClientLog('error', 'api.network_error', error, requestId)
    throw new Error('服务端不可达，请检查 server 是否已启动')
  }
  if (!res.ok) {
    const body = await res.text().catch(() => '')
    writeClientLog('warn', 'api.response_error', `${res.status} ${res.statusText}`, requestId)
    throw new Error(`${res.status} ${res.statusText}${body ? `：${body.slice(0, 160)}` : ''}`)
  }
  writeClientLog('info', 'api.response', `${res.status} ${Math.round(performance.now() - started)}ms`, requestId)
  const text = await res.text()
  return (text ? JSON.parse(text) : undefined) as T
}

export const api = {
  health: () => request<HealthResponse>('/health'),
  browserStatus: () => request<BrowserStatus>('/browser/status'),
  browserConnect: () => request<BrowserConnectResult>('/browser/connect', { method: 'POST' }),
  browserRetry: () => request<BrowserConnectResult>('/browser/retry', { method: 'POST' }),
  listRuns: () => request<CollectionRun[]>('/collection-runs'),
  getRun: (id: string) => request<CollectionRunDetail>(`/collection-runs/${id}`),
  createRun: (body: { target_type: TargetType; target: string; max_items: number }) =>
    request<CollectionRun>('/collection-runs', { method: 'POST', body: JSON.stringify(body) }),
  cancelRun: (id: string) =>
    request<CollectionRun>(`/collection-runs/${id}/cancel`, { method: 'POST' }),
  // app 提交的采集申请。放行 = 服务端建一个真实采集任务并回填 run_id。
  listRequests: () => request<CollectionRequest[]>('/collection-requests'),
  decideRequest: (id: string, body: { action: 'approve' | 'reject'; note?: string }) =>
    request<CollectionRequest>(`/collection-requests/${id}`, {
      method: 'PATCH', body: JSON.stringify(body),
    }),
  listContents: () => request<Content[]>('/contents?limit=20'),
  // 内容池概览：一次取齐每条内容的派生计数，替代逐条探测的 N+1
  listContentsSummary: (limit = 20) => request<ContentSummary[]>(`/contents/summary?limit=${limit}`),
  deleteContent: (id: string) =>
    request<void>(`/contents/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  // app 端「删除」= 软隐藏：只打标记，数据与派生结果完整保留，web 端照常可见
  hideForApp: (id: string) =>
    request<void>(`/contents/${encodeURIComponent(id)}/app-hidden`, { method: 'POST' }),
  // 「重新同步到 app 端」= 取消隐藏。**只有管理员 Cookie 能调**，设备令牌被 401
  restoreForApp: (id: string) =>
    request<void>(`/contents/${encodeURIComponent(id)}/app-hidden`, { method: 'DELETE' }),
  // force=true 为「重新审核」：服务端**新增一个审核批次**，旧批次原样保留
  reviewContent: (id: string, force = false) =>
    request<{ content_id: string; review_status: string; claim_count: number }>(
      `/contents/${encodeURIComponent(id)}/review${force ? '?force=true' : ''}`, { method: 'POST' },
    ),
  // batch_id 省略 → 生效批次（响应里回带全部历史批次）
  getContentReview: (id: string, batchId?: string | null) =>
    request<ContentReviewDetail>(
      `/contents/${encodeURIComponent(id)}/claims${batchId ? `?batch_id=${encodeURIComponent(batchId)}` : ''}`,
    ),
  listReviewBatches: (id: string) => request<ReviewBatch[]>(`/contents/${encodeURIComponent(id)}/review-batches`),
  activateReviewBatch: (id: string, batchId: string) =>
    request<{ content_id: string; review_status: string; claim_count: number }>(
      `/contents/${encodeURIComponent(id)}/review-batches/${encodeURIComponent(batchId)}/activate`,
      { method: 'POST' },
    ),
  decideClaim: (id: string, body: { status: ClaimStatus; rationale: string; evidence_ids: string[] }) =>
    request<{ content_id: string; review_status: string; claim_count: number }>(`/claims/${encodeURIComponent(id)}/decision`, { method: 'POST', body: JSON.stringify(body) }),
  createReport: (content_ids: string[], title: string) => request<Report>('/reports', { method: 'POST', body: JSON.stringify({ content_ids, title }) }),
  listReports: () => request<Report[]>('/reports'),
  listContentReports: (id: string) => request<Report[]>(`/reports?content_id=${encodeURIComponent(id)}`),
  // focus 非空 = 「重新分析」并注入补充视角；服务端只把它当作额外观察侧重，不改判定口径
  analyzeContent: (id: string, focus = '') =>
    request<Analysis>(`/contents/${encodeURIComponent(id)}/analyze`, {
      method: 'POST', body: JSON.stringify({ focus }),
    }),
  listKbDocuments: (limit = 50) => request<KbDocument[]>(`/kb/documents?limit=${limit}`),
  vectorizeContent: (id: string) => request<KbDocument>(`/kb/contents/${encodeURIComponent(id)}`, { method: 'POST' }),
  vectorizeAnalysis: (id: string) => request<KbDocument>(`/kb/analyses/${encodeURIComponent(id)}`, { method: 'POST' }),
  uploadKbFile: (file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return request<KbFileText>('/kb/upload', { method: 'POST', body: fd })
  },
  addKbDocument: (body: { title: string; text: string; tags: string[]; url?: string | null }) =>
    request<KbDocument>('/kb/documents', { method: 'POST', body: JSON.stringify(body) }),
  getKbDocument: (id: string) => request<KbDocument>(`/kb/documents/${encodeURIComponent(id)}`),
  vectorizeKbDocument: (id: string) =>
    request<KbDocument>(`/kb/documents/${encodeURIComponent(id)}/vectorize`, { method: 'POST' }),
  vectorizeKbDocuments: (doc_ids: string[]) =>
    request<KbVectorizeResult[]>('/kb/documents/vectorize', { method: 'POST', body: JSON.stringify({ doc_ids }) }),
  deleteKbDocument: (id: string) =>
    request<void>(`/kb/documents/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  deleteKbDocuments: (doc_ids: string[]) =>
    request<{ deleted: number; requested: number }>('/kb/documents/delete', { method: 'POST', body: JSON.stringify({ doc_ids }) }),
  searchKb: (query: string, top_k = 8) => request<KbSearchHit[]>('/kb/search', { method: 'POST', body: JSON.stringify({ query, top_k }) }),
  askKb: (query: string, top_k?: number, history?: Array<{ query: string; answer: string }>) =>
    request<KbAnswer>('/kb/ask', { method: 'POST', body: JSON.stringify({ query, top_k, history }) }),
  createManualQa: (body: { title: string; pairs: KbQaPairInput[]; tags?: string[]; url?: string | null }) =>
    request<DistillResult>('/kb/qa/documents', { method: 'POST', body: JSON.stringify(body) }),
  distillText: (body: { title: string; text: string; tags?: string[] }) =>
    request<DistillResult>('/kb/qa/distill', { method: 'POST', body: JSON.stringify(body) }),
  distillAnalysis: (analysisId: string, force = false) =>
    request<DistillResult>(`/kb/analyses/${encodeURIComponent(analysisId)}/distill-qa${force ? '?force=true' : ''}`, { method: 'POST' }),
  distillContent: (contentId: string, force = false) =>
    request<DistillResult>(`/kb/contents/${encodeURIComponent(contentId)}/distill-qa${force ? '?force=true' : ''}`, { method: 'POST' }),
  listQaPairs: (docId: string) => request<KbQaPair[]>(`/kb/qa/documents/${encodeURIComponent(docId)}/pairs`),
  updateQaPair: (qaId: string, body: Partial<KbQaPairInput>) =>
    request<KbQaPair>(`/kb/qa/pairs/${encodeURIComponent(qaId)}`, { method: 'PATCH', body: JSON.stringify(body) }),
  approveQaPairs: (qaIds: string[]) =>
    request<KbQaReviewResult>('/kb/qa/pairs/approve', { method: 'POST', body: JSON.stringify({ qa_ids: qaIds }) }),
  rejectQaPairs: (qaIds: string[]) =>
    request<KbQaReviewResult>('/kb/qa/pairs/reject', { method: 'POST', body: JSON.stringify({ qa_ids: qaIds }) }),
  deleteQaPair: (qaId: string) => request<void>(`/kb/qa/pairs/${encodeURIComponent(qaId)}`, { method: 'DELETE' }),
  getAnalysis: (id: string) => request<Analysis>(`/analyses/${encodeURIComponent(id)}`),
  listContentAnalyses: (id: string, limit = 50) => request<Analysis[]>(`/contents/${encodeURIComponent(id)}/analyses?limit=${limit}`),
  login: (username: string, password: string) => request<{ authenticated: boolean; username: string }>('/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }),
  session: () => request<{ authenticated: boolean; username: string }>('/auth/session'),
  logout: () => request<{ authenticated: boolean }>('/auth/logout', { method: 'POST' }),
}
