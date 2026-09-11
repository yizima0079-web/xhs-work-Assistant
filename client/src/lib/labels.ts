import type { ClaimStatus, RequestStatus, RunStatus, TargetType } from '../types'

/** 采集任务状态 → 中文标签 + 语义色（浅色主题 600 级，与 CSS token 同步）。 */
export const RUN_STATUS: Record<RunStatus, { label: string; color: string }> = {
  queued: { label: '排队中', color: '#64748b' },
  running: { label: '采集中', color: '#2563eb' },
  retry_wait: { label: '重试等待', color: '#b45309' },
  success: { label: '成功', color: '#16a34a' },
  partial: { label: '部分成功', color: '#d97706' },
  blocked: { label: '已熔断', color: '#dc2626' },
  failed: { label: '失败', color: '#dc2626' },
  cancelled: { label: '已取消', color: '#6b7888' },
}

export const RUN_ORDER: RunStatus[] = [
  'running', 'queued', 'retry_wait', 'success', 'partial', 'blocked', 'failed', 'cancelled',
]

/** 活跃（进行中）状态集合：用于 KPI 与状态点。 */
export const ACTIVE_RUNS: RunStatus[] = ['running', 'queued', 'retry_wait']
export const ATTENTION_RUNS: RunStatus[] = ['blocked', 'failed']

export const RUN_STATE_COLOR: Record<RunStatus, string> = Object.fromEntries(
  (Object.keys(RUN_STATUS) as RunStatus[]).map((s) => [s, RUN_STATUS[s].color]),
) as Record<RunStatus, string>

/** app 采集申请状态 → 中文标签 + 语义色。 */
export const REQUEST_STATUS: Record<RequestStatus, { label: string; color: string }> = {
  pending: { label: '待放行', color: '#d97706' },
  approved: { label: '已放行', color: '#16a34a' },
  rejected: { label: '已驳回', color: '#dc2626' },
}

export const TARGET_TYPE_LABEL: Record<TargetType, string> = {
  url: 'URL 详情',
  keyword: '关键词',
  account: '账号',
  post: '笔记',
}

/** 内容审核状态 → 中文标签 + 语义色。review_status 为字符串（服务端域枚举转小写）。 */
export const REVIEW_META: Record<string, { label: string; color: string }> = {
  pending: { label: '未审核', color: '#64748b' },
  extracted: { label: '待人工', color: '#d97706' },
  approved: { label: '可信', color: '#16a34a' },
  rejected: { label: '不采信', color: '#dc2626' },
  disputed: { label: '存争议', color: '#7c3aed' },
}

export const REVIEW_ORDER = ['pending', 'extracted', 'approved', 'rejected', 'disputed'] as const

/** 断言人工复核状态 → 语义色（浅色主题）。 */
export const CLAIM_STATUS_COLOR: Record<ClaimStatus, string> = {
  unverified: '#64748b',
  supported: '#16a34a',
  contradicted: '#dc2626',
  unclear: '#b45309',
}

export const CLAIM_STATUS_LABEL: Record<ClaimStatus, string> = {
  unverified: '未验证',
  supported: '支持',
  contradicted: '反驳',
  unclear: '存疑',
}

export const CLAIM_TYPE_LABEL: Record<string, string> = {
  fact: '事实',
  opinion: '观点',
  prediction: '预判',
  promotion: '推广',
}

export function reviewLabel(status: string): string {
  return REVIEW_META[status]?.label ?? status
}

export function reviewColor(status: string): string {
  return REVIEW_META[status]?.color ?? '#8f9dad'
}

export function platformLabel(platform: string): string {
  const map: Record<string, string> = {
    xiaohongshu: '小红书',
    xhs: '小红书',
    notes: '小红书',
    rednote: '小红书',
    weibo: '微博',
    twitter: 'X',
    x: 'X',
    douyin: '抖音',
    bilibili: 'B站',
  }
  return map[platform] ?? platform
}
