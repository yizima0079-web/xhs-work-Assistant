import { Fragment, memo, useCallback, useEffect, useMemo, useState } from 'react'
import type { CSSProperties, SyntheticEvent } from 'react'
import { api } from '../api/client'
import type {
  Analysis,
  AnalysisReason,
  Claim,
  ClaimStatus,
  CollectionRequest,
  CollectionRun,
  CollectionRunDetail,
  Content,
  ContentReviewDetail,
  ContentSummary,
  KbAnswer,
  Report,
  ReviewBatch,
  RunStatus,
  TargetType,
} from '../types'
import {
  ACTIVE_RUNS,
  CLAIM_STATUS_LABEL,
  CLAIM_TYPE_LABEL,
  CLAIM_STATUS_COLOR,
  REVIEW_META,
  REQUEST_STATUS,
  REVIEW_ORDER,
  RUN_ORDER,
  RUN_STATUS,
  TARGET_TYPE_LABEL,
  platformLabel,
  reviewColor,
  reviewLabel,
} from '../lib/labels'
import { fmtCompact, fmtCount, sum, toNum } from '../lib/format'
import { keepIfSame, keepItemsById } from '../lib/stable'
import Donut, { type DonutSegment } from '../components/viz/Donut'
import RankBars from '../components/viz/RankBars'
import MiniColumns from '../components/viz/MiniColumns'
import BrowserPanel from '../components/BrowserPanel'
import EngagementBar from '../components/viz/EngagementBar'
import { VersionBar, type VersionOption } from '../components/VersionBar'
import { ConfirmDialog } from '../components/ConfirmDialog'

const CONTENT_WINDOW = 20

// 分级轮询间隔：有任务在跑才快刷，空闲时慢刷。
// 原来的固定 2s 会白刷大量请求，还把「后台刷新中」混进了按钮文案（表单文字闪回的根因）。
const TICK_HEALTH = 30_000
const TICK_RUNS_BUSY = 2_000
const TICK_RUNS_IDLE = 10_000
const TICK_CONTENTS = 10_000

/** 新→旧排列的版本列表里，第 index 项是「第 (total - index) 版」。 */
function versionLabel(index: number, total: number): string {
  return `第 ${total - index} 版`
}

function stamp(at: string | null | undefined): string {
  return at ? at.slice(0, 16).replace('T', ' ') : '—'
}

/** 不可逆操作的二次确认参数。 */
interface ConfirmRequest {
  title: string
  message?: string
  items?: string[]
  note?: string
  confirmLabel?: string
  danger?: boolean
  onConfirm: () => void | Promise<void>
}

/** 内嵌在内容卡片正下方的结果类型。 */
type InlineView = 'analysis' | 'review' | 'report'

/** 下一步引导的单个动作。 */
interface NextStep {
  label: string
  hint: string
  onClick: () => void
  done?: boolean
}

/** 内容类型 badge 文案（xhs note.type: normal/video，服务器归一 note/video/image/mixed）。 */
const CONTENT_TYPE_LABEL: Record<string, string> = {
  note: '图文',
  video: '视频',
  image: '图片',
  mixed: '多图',
}

/** 作品分析结论文案 + 语义色（爆款 ok / 平淡 amber / 不确定 slate）。 */
const ANALYSIS_VERDICT: Record<string, { label: string; color: string }> = {
  viral: { label: '爆款', color: '#16a34a' },
  flat: { label: '表现平淡', color: '#f59e0b' },
  uncertain: { label: '不确定', color: '#64748b' },
}

function runMeta(status: string): { label: string; color: string } {
  return RUN_STATUS[status as RunStatus] ?? { label: status, color: '#64748b' }
}

/** 以状态语义色渲染 chip（color-mix 淡底 + 描边）。 */
function chipStyle(color: string): CSSProperties {
  return {
    color,
    background: `color-mix(in srgb, ${color} 13%, transparent)`,
    borderColor: `color-mix(in srgb, ${color} 40%, transparent)`,
  }
}

function engagementScore(c: Content): number {
  const e = c.engagement ?? {}
  return sum([toNum(e.likes), toNum(e.comments), toNum(e.shares), toNum(e.collects)])
}

type ComparisonDimension = {
  label: string
  a: string
  b: string
  note: string
  winner: 'a' | 'b' | 'tie' | 'none'
}

type ComparisonResult = {
  a: Content
  b: Content
  analyses: [Analysis | null, Analysis | null]
  kb: KbAnswer
  dimensions: ComparisonDimension[]
  createdAt: string
}

function contentLabel(content: Content): string {
  return content.title?.trim() || content.text?.trim().slice(0, 28) || content.content_id.slice(0, 12)
}

function contentEngagementParts(content: Content): [number, number, number, number] {
  const e = content.engagement ?? {}
  return [toNum(e.likes), toNum(e.comments), toNum(e.shares), toNum(e.collects)]
}

function compareWinner(a: number, b: number): ComparisonDimension['winner'] {
  if (a === 0 && b === 0) return 'none'
  if (Math.abs(a - b) < Math.max(a, b) * 0.03) return 'tie'
  return a > b ? 'a' : 'b'
}

function buildComparisonDimensions(a: Content, b: Content): ComparisonDimension[] {
  const av = contentEngagementParts(a)
  const bv = contentEngagementParts(b)
  const aTotal = sum(av)
  const bTotal = sum(bv)
  const aDeep = sum(av.slice(1))
  const bDeep = sum(bv.slice(1))
  const aText = (a.title ?? '').length + (a.text ?? '').length
  const bText = (b.title ?? '').length + (b.text ?? '').length
  const aTags = a.tags?.length ?? 0
  const bTags = b.tags?.length ?? 0
  return [
    { label: '总互动', a: fmtCompact(aTotal), b: fmtCompact(bTotal), note: '点赞、评论、转发、收藏之和；仅代表当前采集快照', winner: compareWinner(aTotal, bTotal) },
    { label: '点赞', a: fmtCompact(av[0]), b: fmtCompact(bv[0]), note: '轻互动规模', winner: compareWinner(av[0], bv[0]) },
    { label: '评论', a: fmtCompact(av[1]), b: fmtCompact(bv[1]), note: '讨论意愿', winner: compareWinner(av[1], bv[1]) },
    { label: '转发', a: fmtCompact(av[2]), b: fmtCompact(bv[2]), note: '扩散意愿', winner: compareWinner(av[2], bv[2]) },
    { label: '收藏', a: fmtCompact(av[3]), b: fmtCompact(bv[3]), note: '复用或决策价值信号', winner: compareWinner(av[3], bv[3]) },
    { label: '深度互动占比', a: aTotal ? `${Math.round((aDeep / aTotal) * 100)}%` : '—', b: bTotal ? `${Math.round((bDeep / bTotal) * 100)}%` : '—', note: '评论、转发、收藏 ÷ 总互动；不是平台官方转化率', winner: compareWinner(aTotal ? aDeep / aTotal : 0, bTotal ? bDeep / bTotal : 0) },
    { label: '文本体量', a: `${aText} 字`, b: `${bText} 字`, note: '标题 + 正文字符数，仅作结构对照，不代表质量', winner: 'none' },
    { label: '标签数', a: `${aTags}`, b: `${bTags}`, note: '采集到的标签数量', winner: 'none' },
  ]
}

function buildComparisonMarkdown(result: ComparisonResult): string {
  const lines: string[] = [
    `# 内容对比：${contentLabel(result.a)} vs ${contentLabel(result.b)}`,
    '',
    `- 内容 A：${contentLabel(result.a)}（${result.a.content_id}）`,
    `- 内容 B：${contentLabel(result.b)}（${result.b.content_id}）`,
    `- 采集时间：A ${result.a.collected_at}；B ${result.b.collected_at}`,
    '- 可信分层：互动指标为标准化快照；机制结论为模型观点；案例来自 RAG 引用。',
    '',
    '## 一、真实数据多维对比',
    '',
    '| 维度 | 内容 A | 内容 B | 相对表现 |',
    '| --- | ---: | ---: | --- |',
  ]
  for (const dimension of result.dimensions) {
    const winner = dimension.winner === 'a' ? 'A' : dimension.winner === 'b' ? 'B' : dimension.winner === 'tie' ? '接近' : '—'
    lines.push(`| ${dimension.label} | ${dimension.a} | ${dimension.b} | ${winner} |`)
  }
  const addReasons = (label: string, analysis: Analysis | null) => {
    lines.push('', `## ${label} 机制观点`)
    if (!analysis) { lines.push('暂无单条分析结果。'); return }
    lines.push(analysis.payload.summary || '暂无摘要。')
    const reasons = analysis.verdict === 'viral' ? analysis.payload.viral_reasons : analysis.payload.flat_reasons
    for (const reason of reasons) lines.push(`- ${reason.factor}：${reason.evidence}（置信度 ${Math.round(reason.confidence * 100)}%）`)
    for (const suggestion of analysis.payload.suggestions ?? []) lines.push(`- 建议：${suggestion}`)
  }
  addReasons('内容 A', result.analyses[0])
  addReasons('内容 B', result.analyses[1])
  lines.push('', '## RAG 案例证据', '', result.kb.answer || '本次没有检索到可引用答案。')
  for (const citation of result.kb.citations) {
    lines.push(`- [${citation.verified ? '已审核' : '待审核'}] ${citation.title}（文档 ${citation.doc_id}，匹配 ${Math.round(citation.score * 100)}%）：${citation.text}`)
  }
  lines.push('', '## 局限', '', ...(result.kb.limitations.length ? result.kb.limitations.map((x) => `- ${x}`) : ['- 互动快照不包含曝光量，不能单独证明因果。']))
  return lines.join('\n')
}

export default function Dashboard({ onNav }: { onNav: () => void }) {
  const [runs, setRuns] = useState<CollectionRun[]>([])
  // app 端提交的采集申请。放行 = 建真实采集任务，所以这条与 runs 同一节奏轮询。
  const [requests, setRequests] = useState<CollectionRequest[]>([])
  const [contents, setContents] = useState<Content[]>([])
  // 内容池每行的派生计数（分析/报告/断言/批次/已入库），一次 /contents/summary 取齐，
  // 替代原来「每条内容再发 2 条请求探测」的 N+1（20 条内容 = 40 条请求）。
  const [summaries, setSummaries] = useState<Record<string, ContentSummary | undefined>>({})
  // 三类结果都内嵌在对应内容卡片正下方，因此按 content_id 分桶，而不是全局单例。
  // 分析是**多版本**列表（新→旧），不是单条 —— 重析不再覆盖历史版本。
  const [analyses, setAnalyses] = useState<Record<string, Analysis[] | undefined>>({})
  const [reviews, setReviews] = useState<Record<string, ContentReviewDetail | undefined>>({})
  const [reports, setReports] = useState<Record<string, Report[] | undefined>>({})
  // 用户在各面板里挑中的版本：不选 → 默认最新版 / 生效批次
  const [analysisPick, setAnalysisPick] = useState<Record<string, string | undefined>>({})
  const [reviewPick, setReviewPick] = useState<Record<string, string | undefined>>({})
  const [inline, setInline] = useState<{ contentId: string; view: InlineView } | null>(null)
  const [report, setReport] = useState<Report | null>(null)  // 跨内容报告（批量生成），仍留列表下方
  const [panelBusy, setPanelBusy] = useState<string | null>(null)
  const [analysisBusy, setAnalysisBusy] = useState<string | null>(null)
  const [kbBusy, setKbBusy] = useState(false)
  const [distillBusy, setDistillBusy] = useState(false)
  const [batchBusy, setBatchBusy] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  // 「重新同步到 app 端」单独占用一个 busy：和 busy 共用会让审核按钮在恢复期间
  // 显示「审核中…」，用户看到的是一次莫名其妙的审核
  const [restoring, setRestoring] = useState<string | null>(null)
  const [selected, setSelected] = useState<CollectionRunDetail | null>(null)
  const [targetType, setTargetType] = useState<TargetType>('keyword')
  const [target, setTarget] = useState('')
  const [maxItems, setMaxItems] = useState(2)
  const [error, setError] = useState<string | null>(null)
  // submitting 只代表「用户点了创建」；后台轮询另用 syncing。两者混用会让提交按钮
  // 每 2 秒在「创建任务 / 创建中…」之间翻转，那就是表单文字闪回的根因。
  const [submitting, setSubmitting] = useState(false)
  const [syncing, setSyncing] = useState(false)
  const [lastUpdated, setLastUpdated] = useState<string | null>(null)
  const [confirm, setConfirm] = useState<ConfirmRequest | null>(null)
  const [confirmBusy, setConfirmBusy] = useState(false)
  const [compareOpen, setCompareOpen] = useState(false)
  const [compareIds, setCompareIds] = useState<[string, string]>(['', ''])
  const [comparison, setComparison] = useState<ComparisonResult | null>(null)
  const [compareBusy, setCompareBusy] = useState(false)
  const [compareMessage, setCompareMessage] = useState<string | null>(null)
  // 「重新分析」的角度注入：focusOpen 记录展开输入框的内容，focusDraft 存草稿
  const [focusOpen, setFocusOpen] = useState<string | null>(null)
  const [focusDraft, setFocusDraft] = useState<Record<string, string>>({})

  useEffect(() => {
    if (contents.length < 2) return
    setCompareIds((prev) => {
      const valid = new Set(contents.map((c) => c.content_id))
      const a = valid.has(prev[0]) ? prev[0] : contents[0].content_id
      const b = valid.has(prev[1]) && prev[1] !== a
        ? prev[1]
        : contents.find((c) => c.content_id !== a)?.content_id ?? ''
      return a === prev[0] && b === prev[1] ? prev : [a, b]
    })
  }, [contents])

  // ---- 轮询：三条独立节奏的循环，互不牵连 ----
  // /health 内部会 spawn 一次 OpenCLI whoami 子进程，降到 30s（后端另有 TTL 缓存）。
  useEffect(() => {
    let active = true
    let timer: number | undefined
    let busyNow = false
    const tick = async () => {
      if (!active) return
      if (!busyNow) {
        busyNow = true
        try { await api.health() } catch { /* 健康探测失败不打断看板，只有下方数据循环才报错 */ }
        finally { busyNow = false }
      }
      if (active) timer = window.setTimeout(tick, TICK_HEALTH)
    }
    tick()
    return () => { active = false; if (timer) window.clearTimeout(timer) }
  }, [])

  // 任务列表 + app 采集申请：有 running/queued **或有待放行申请**就 2s，空闲 10s。
  // 两者合一个 tick：放行会立刻造出 run，分开轮询会让「放行后任务列表还是旧的」闪一下。
  useEffect(() => {
    let active = true
    let timer: number | undefined
    let busyNow = false
    const tick = async () => {
      if (!active) return
      let delay = TICK_RUNS_IDLE
      if (!busyNow) {
        busyNow = true
        try {
          const [next, nextRequests] = await Promise.all([api.listRuns(), api.listRequests()])
          if (!active) return
          // 内容没变就保持原引用 → 不触发重渲染、不重算 useMemo、不打断入场动画
          setRuns((prev) => keepIfSame(prev, next))
          setRequests((prev) => keepIfSame(prev, nextRequests))
          delay = next.some((r) => ACTIVE_RUNS.includes(r.status as RunStatus))
            || nextRequests.some((r) => r.status === 'pending')
            ? TICK_RUNS_BUSY : TICK_RUNS_IDLE
        } catch (e) {
          if (active) setError(e instanceof Error ? e.message : '加载失败')
        } finally { busyNow = false }
      }
      if (active) timer = window.setTimeout(tick, delay)
    }
    tick()
    return () => { active = false; if (timer) window.clearTimeout(timer) }
  }, [])

  // 内容池 + 概览：10s。两条请求合并成一个 tick，保证计数与列表同批更新。
  useEffect(() => {
    let active = true
    let timer: number | undefined
    let busyNow = false
    const tick = async () => {
      if (!active) return
      if (!busyNow) {
        busyNow = true
        setSyncing(true)
        try {
          const [nextContents, nextSummaries] = await Promise.all([
            api.listContents(), api.listContentsSummary(CONTENT_WINDOW),
          ])
          if (!active) return
          setContents((prev) => keepItemsById(prev, nextContents, (c) => c.content_id))
          setSummaries((prev) => keepIfSame(prev, Object.fromEntries(
            nextSummaries.map((s) => [s.content_id, s]),
          )))
          setLastUpdated(new Date().toISOString())
          setError(null)
        } catch (e) {
          if (active) setError(e instanceof Error ? e.message : '加载失败')
        } finally {
          busyNow = false
          if (active) setSyncing(false)
        }
      }
      if (active) timer = window.setTimeout(tick, TICK_CONTENTS)
    }
    tick()
    return () => { active = false; if (timer) window.clearTimeout(timer) }
  }, [])

  const selectRun = useCallback(async (id: string) => {
    if (selected?.run.id === id) { setSelected(null); return }
    try {
      setSelected(await api.getRun(id))
    } catch (e) {
      setError(e instanceof Error ? e.message : '任务详情加载失败')
    }
  }, [selected])

  const create = async () => {
    if (!target.trim() || submitting) return
    setSubmitting(true)
    setError(null)
    try {
      await api.createRun({ target_type: targetType, target: target.trim(), max_items: maxItems })
      setTarget('')
      // 新任务立即入列，不等下一次轮询 —— 否则点完「创建任务」要愣 2 秒才看到行
      const next = await api.listRuns()
      setRuns((prev) => keepIfSame(prev, next))
    } catch (e) {
      setError(e instanceof Error ? e.message : '创建任务失败')
    } finally {
      setSubmitting(false)
    }
  }

  const cancel = async (id: string) => {
    try {
      await api.cancelRun(id)
      const next = await api.listRuns()
      setRuns((prev) => keepIfSame(prev, next))
    } catch (e) {
      setError(e instanceof Error ? e.message : '取消任务失败')
    }
  }

  /** app 采集申请的放行 / 驳回 —— 都走二次确认：放行会真的触发一次平台采集，驳回不可撤销。 */
  const decideRequest = (req: CollectionRequest, action: 'approve' | 'reject') => {
    const approve = action === 'approve'
    setConfirm({
      title: approve ? '放行这条采集申请？' : '驳回这条采集申请？',
      message: approve
        ? `将以关键词「${req.target}」创建采集任务，最多 ${req.max_items} 条，立即用浏览器登录态访问平台。`
        : `「${req.target}」这条申请将被标记为已驳回，app 端会看到该结果。`,
      note: approve
        ? '采集会真实访问平台（低频少量、全局并发 1）。任务创建后不可撤回，只能取消。'
        : '驳回后不可撤销；如需重新采集，请 app 端重新提交。',
      confirmLabel: approve ? '放行并采集' : '驳回',
      danger: !approve,
      onConfirm: async () => {
        await api.decideRequest(req.id, { action })
        // 放行会立刻造出一个 run，两条列表一起刷新，避免看到「申请已放行但任务列表还是旧的」
        const [nextRequests, nextRuns] = await Promise.all([api.listRequests(), api.listRuns()])
        setRequests((prev) => keepIfSame(prev, nextRequests))
        setRuns((prev) => keepIfSame(prev, nextRuns))
        setConfirm(null)
      },
    })
  }

  /** 打开/收起某内容的内嵌结果面板，按需拉取对应数据（列表页不再逐条预取）。 */
  const openInline = useCallback(async (content: Content, view: InlineView) => {
    const id = content.content_id
    // 已开在同一面板 → 收起，不再发请求。判据必须取自本次渲染的 inline：
    // setState 的 updater 要到重渲染才执行，在它里面写外部变量再立刻读是读不到的。
    if (inline?.contentId === id && inline.view === view) {
      setInline(null)
      return
    }
    setInline({ contentId: id, view })
    try {
      if (view === 'analysis') {
        setPanelBusy(`analysis:${id}`)
        const list = await api.listContentAnalyses(id)
        setAnalyses((prev) => ({ ...prev, [id]: list }))
      } else if (view === 'review') {
        setPanelBusy(`review:${id}`)
        const detail = await api.getContentReview(id)
        setReviews((prev) => ({ ...prev, [id]: detail }))
      } else {
        setPanelBusy(`report:${id}`)
        const list = await api.listContentReports(id)
        setReports((prev) => ({ ...prev, [id]: list }))
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '详情加载失败')
    } finally {
      setPanelBusy(null)
    }
  }, [inline])

  /** 重新拉取某内容的审核详情（保留用户当前挑中的批次）。 */
  const refreshReview = useCallback(async (contentId: string, batchId?: string | null) => {
    const detail = await api.getContentReview(contentId, batchId)
    setReviews((prev) => ({ ...prev, [contentId]: detail }))
    return detail
  }, [])

  const runReview = useCallback(async (content: Content, force = false) => {
    const id = content.content_id
    setBusy(id)
    setError(null)
    try {
      await api.reviewContent(id, force)
      const detail = await refreshReview(id)
      // 新批次即生效批次，面板自动跟到新版
      setReviewPick((prev) => ({ ...prev, [id]: detail.batch_id ?? undefined }))
      setInline({ contentId: id, view: 'review' })
    } catch (e) {
      setError(e instanceof Error ? e.message : force ? '重新审核失败' : '自动审核失败')
    } finally {
      setBusy(null)
    }
  }, [refreshReview])

  const decide = useCallback(async (claimId: string, status: ClaimStatus) => {
    const contentId = inline?.contentId
    if (!contentId) return
    setBusy(claimId)
    try {
      await api.decideClaim(claimId, { status, rationale: '客户端人工复核', evidence_ids: [] })
      await refreshReview(contentId, reviews[contentId]?.batch_id ?? undefined)
    } catch (e) {
      setError(e instanceof Error ? e.message : '人工复核失败')
    } finally {
      setBusy(null)
    }
  }, [inline, reviews, refreshReview])

  const createReport = async () => {
    if (!contents.length) return
    setBusy('report')
    try { setReport(await api.createReport(contents.map((c) => c.content_id), '内容审核报告')) }
    catch (e) { setError(e instanceof Error ? e.message : '报告生成失败') }
    finally { setBusy(null) }
  }

  /** 生成/重新生成单篇报告，结果内嵌回该内容卡片下方（旧版保留在版本列表里）。 */
  const createContentReport = useCallback(async (content: Content, force = false) => {
    const id = content.content_id
    setBatchBusy(`report:${id}`)
    setError(null)
    try {
      const created = await api.createReport([id], `${content.title || '单条内容'}审核报告`)
      setReports((prev) => ({ ...prev, [id]: [created, ...(prev[id] ?? [])] }))
      setInline({ contentId: id, view: 'report' })
    } catch (e) {
      setError(e instanceof Error ? e.message : force ? '重新生成报告失败' : '审核报告生成失败')
    } finally {
      setBatchBusy(null)
    }
  }, [])

  const analyzeContent = useCallback(async (content: Content, focus = '') => {
    const id = content.content_id
    setAnalysisBusy(id)
    setError(null)
    try {
      const a = await api.analyzeContent(id, focus)
      // 追加为新一版（列表新→旧），不覆盖 —— 这就是「重析后还能挑第 2 版」的基础
      setAnalyses((prev) => ({ ...prev, [id]: [a, ...(prev[id] ?? [])] }))
      setAnalysisPick((prev) => ({ ...prev, [id]: a.analysis_id }))
      setSummaries((prev) => {
        const s = prev[id]
        return s ? { ...prev, [id]: { ...s, analysis_count: s.analysis_count + 1, latest_analysis_id: a.analysis_id, latest_analysis_at: a.created_at } } : prev
      })
      setInline({ contentId: id, view: 'analysis' })
      setFocusOpen(null)
      setFocusDraft((prev) => ({ ...prev, [id]: '' }))
    } catch (e) {
      setError(e instanceof Error ? e.message : focus ? '重新分析失败' : '作品分析失败')
    } finally {
      setAnalysisBusy(null)
    }
  }, [])

  const runComparison = async () => {
    if (compareBusy || !compareIds[0] || !compareIds[1] || compareIds[0] === compareIds[1]) return
    const a = contents.find((c) => c.content_id === compareIds[0])
    const b = contents.find((c) => c.content_id === compareIds[1])
    if (!a || !b) return
    setCompareBusy(true)
    setComparison(null)
    setCompareMessage(null)
    setError(null)
    try {
      const loadLatest = async (content: Content, other: Content): Promise<Analysis | null> => {
        const cached = analyses[content.content_id]?.[0]
        if (cached) return cached
        try {
          const list = await api.listContentAnalyses(content.content_id)
          if (list[0]) {
            setAnalyses((prev) => ({ ...prev, [content.content_id]: list }))
            return list[0]
          }
          const generated = await api.analyzeContent(
            content.content_id,
            `这是一次双内容对比。请重点观察本内容与另一条内容 ${other.content_id} 在标题钩子、内容结构、互动结构、受众价值和传播机制上的差异。`
          )
          setAnalyses((prev) => ({ ...prev, [content.content_id]: [generated, ...(prev[content.content_id] ?? [])] }))
          setSummaries((prev) => {
            const current = prev[content.content_id]
            return current
              ? { ...prev, [content.content_id]: { ...current, analysis_count: current.analysis_count + 1, latest_analysis_id: generated.analysis_id, latest_analysis_at: generated.created_at } }
              : prev
          })
          return generated
        } catch (e) {
          setCompareMessage(`${contentLabel(content)} 的单条分析未完成，已保留指标对比：${e instanceof Error ? e.message : '分析服务不可用'}`)
          return null
        }
      }

      // 模型请求和 RAG 请求保持串行，避免并发放大；已有分析直接复用，不重复写入版本。
      const analysisA = await loadLatest(a, b)
      const analysisB = await loadLatest(b, a)
      const metrics = buildComparisonDimensions(a, b)
      const query = [
        '请基于知识库中已审核、可引用的内容，辅助比较下面两条内容为什么一条表现更强、另一条表现平淡。',
        `内容A：${contentLabel(a)}（${a.content_id}）`,
        `内容B：${contentLabel(b)}（${b.content_id}）`,
        `A标签：${(a.tags ?? []).join('、') || '无'}；B标签：${(b.tags ?? []).join('、') || '无'}`,
        `A正文：${(a.text ?? '').slice(0, 500)}；B正文：${(b.text ?? '').slice(0, 500)}`,
        `客观互动快照：A总互动 ${fmtCompact(engagementScore(a))}，B总互动 ${fmtCompact(engagementScore(b))}。`,
        '请只使用检索到的知识库内容作为外部例证，明确区分知识库证据、内容自身数据和推断；给出可执行的改进建议。',
      ].join('\n')
      let kb: KbAnswer = {
        answered: false,
        answer: '',
        citations: [],
        top_score: 0,
        retrieval_count: 0,
        limitations: ['RAG 检索未完成，当前结果不含知识库案例。'],
        reason: 'rag_unavailable',
      }
      try {
        kb = await api.askKb(query, 8)
      } catch (e) {
        setCompareMessage((prev) => `${prev ? `${prev}；` : ''}RAG 检索未完成，已保留内容指标与单条分析：${e instanceof Error ? e.message : '知识库服务不可用'}`)
      }
      setComparison({ a, b, analyses: [analysisA, analysisB], kb, dimensions: metrics, createdAt: new Date().toISOString() })
    } catch (e) {
      setError(e instanceof Error ? e.message : '对比分析失败')
    } finally {
      setCompareBusy(false)
    }
  }

  const saveComparisonText = async (result: ComparisonResult) => {
    if (kbBusy) return
    setKbBusy(true)
    setCompareMessage(null)
    setError(null)
    const text = buildComparisonMarkdown(result)
    try {
      const doc = await api.addKbDocument({
        title: `内容对比：${contentLabel(result.a)} vs ${contentLabel(result.b)}`,
        text,
        tags: ['内容对比', '爆款分析', result.a.platform, result.b.platform].filter(Boolean),
      })
      try {
        await api.vectorizeKbDocument(doc.doc_id)
        setCompareMessage(`对比文本已向量化入库：${doc.title}`)
      } catch (e) {
        setCompareMessage(`对比文本已保存为待向量化文档「${doc.title}」，向量化失败：${e instanceof Error ? e.message : '未知错误'}`)
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '对比文本入库失败')
    } finally {
      setKbBusy(false)
    }
  }

  const distillComparisonToQa = async (result: ComparisonResult) => {
    if (distillBusy) return
    setDistillBusy(true)
    setCompareMessage(null)
    setError(null)
    try {
      const text = buildComparisonMarkdown(result)
      const distilled = await api.distillText({
        title: `内容对比 Q&A：${contentLabel(result.a)} vs ${contentLabel(result.b)}`,
        text,
        tags: ['内容对比', '爆款分析', 'Q&A'],
      })
      setCompareMessage(`已生成 ${distilled.pairs.length} 条 Q&A 草稿；请到知识库审核通过后再向量化入库。`)
    } catch (e) {
      setError(e instanceof Error ? e.message : '对比内容 Q&A 蒸馏失败')
    } finally {
      setDistillBusy(false)
    }
  }

  const runBulkAnalysis = async () => {
    if (batchBusy || analysisBusy || contents.length === 0) return
    setBatchBusy('analysis')
    setError(null)
    try {
      for (const content of contents) {
        setAnalysisBusy(content.content_id)
        try {
          const result = await api.analyzeContent(content.content_id)
          const id = content.content_id
          setAnalyses((prev) => ({ ...prev, [id]: [result, ...(prev[id] ?? [])] }))
        } finally {
          setAnalysisBusy(null)
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : '批量分析失败')
    } finally {
      setBatchBusy(null)
    }
  }

  const runBulkReview = async () => {
    if (batchBusy || contents.length === 0) return
    setBatchBusy('review')
    setError(null)
    try {
      for (const content of contents) await api.reviewContent(content.content_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : '批量审核失败')
    } finally {
      setBatchBusy(null)
    }
  }

  /** 把**选中的那一版**分析分块向量化入知识库。 */
  const saveAnalysisToKb = useCallback(async (a: Analysis) => {
    if (kbBusy) return
    setKbBusy(true)
    setError(null)
    try {
      await api.vectorizeAnalysis(a.analysis_id)
      // 以服务端 in_kb 为准回填：入库成功才标「✓已入库」
      setAnalyses((prev) => {
        const list = prev[a.content_id]
        if (!list) return prev
        return {
          ...prev,
          [a.content_id]: list.map((x) => (x.analysis_id === a.analysis_id ? { ...x, in_kb: true } : x)),
        }
      })
    } catch (e) {
      setError(e instanceof Error ? e.message : '入库失败')
      throw e
    } finally {
      setKbBusy(false)
    }
  }, [kbBusy])

  /** 把**选中的那一版**分析蒸馏为 Q&A 知识草稿（走人工审核后入库）。 */
  const distillAnalysisToQa = useCallback(async (a: Analysis) => {
    if (distillBusy) return
    setDistillBusy(true)
    setError(null)
    try {
      await api.distillAnalysis(a.analysis_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : '蒸馏失败')
      throw e
    } finally {
      setDistillBusy(false)
    }
  }, [distillBusy])

  /** 切换要查看的审核批次：必须回后端取该批次的断言，不能只改本地选中态。 */
  const pickBatch = useCallback(async (content: Content, batchId: string) => {
    setReviewPick((prev) => ({ ...prev, [content.content_id]: batchId }))
    try {
      await refreshReview(content.content_id, batchId)
    } catch (e) {
      setError(e instanceof Error ? e.message : '批次加载失败')
    }
  }, [refreshReview])

  /** 展开/收起某内容的「重析补充视角」输入框。稳定引用，避免打散 ContentCard 的 memo。 */
  const toggleFocus = useCallback((content: Content) => {
    const id = content.content_id
    setFocusOpen((prev) => (prev === id ? null : id))
  }, [])

  const activateBatch = useCallback(async (contentId: string, batchId: string) => {
    setBusy(contentId)
    setError(null)
    try {
      await api.activateReviewBatch(contentId, batchId)
      await refreshReview(contentId, batchId)
    } catch (e) {
      setError(e instanceof Error ? e.message : '切换生效版本失败')
    } finally {
      setBusy(null)
    }
  }, [refreshReview])

  /** 内容池删除：不可逆，走二次确认；确认弹窗里列出逐项影响面。 */
  const confirmDeleteContent = useCallback((content: Content) => {
    const s = summaries[content.content_id]
    const items = s ? [
      `断言 ${s.claim_count} 条（含 ${s.review_batch_count} 个审核版本）`,
      `作品分析 ${s.analysis_count} 版`,
      `审核报告 ${s.report_count} 篇`,
      `知识库文档 ${s.kb_doc_count} 篇（含已向量化的分块）`,
      content.cover_local ? '本地封面图片文件' : '',
    ].filter(Boolean) : ['该内容关联的断言 / 分析 / 报告 / 知识库文档']
    setConfirm({
      title: '删除这条内容？',
      message: `「${content.title || content.text?.slice(0, 30) || content.content_id.slice(0, 8)}」及其全部下游产物将被永久删除，不可恢复。`,
      items,
      note: '删除后该内容会从所有相关报告中摘除；报告若因此变空会一并删除。',
      confirmLabel: '永久删除',
      danger: true,
      onConfirm: async () => {
        await api.deleteContent(content.content_id)
        const id = content.content_id
        setContents((prev) => prev.filter((c) => c.content_id !== id))
        setSummaries((prev) => { const next = { ...prev }; delete next[id]; return next })
        setAnalyses((prev) => { const next = { ...prev }; delete next[id]; return next })
        setReviews((prev) => { const next = { ...prev }; delete next[id]; return next })
        setReports((prev) => { const next = { ...prev }; delete next[id]; return next })
        setInline((prev) => (prev?.contentId === id ? null : prev))
        setConfirm(null)
      },
    })
  }, [summaries])

  /** 「重新同步到 app 端」= 取消隐藏，app 下次拉取即恢复显示。
   *
   * 不弹二次确认：与真删相反，这个动作**完全可逆**（再隐藏一次就回去了），
   * 弹窗只会让恢复变成一件有心理成本的事 —— 而恢复恰恰是管理员被鼓励去点的。
   * 本地直接改这条的 `app_hidden_at`，不重拉列表（照 confirmDeleteContent 的做法）。
   */
  const restoreToApp = useCallback(async (content: Content) => {
    const id = content.content_id
    setRestoring(id)
    setError(null)
    try {
      await api.restoreForApp(id)
      setContents((prev) => prev.map((c) => (
        c.content_id === id ? { ...c, app_hidden_at: null } : c
      )))
    } catch (e) {
      setError(e instanceof Error ? e.message : '恢复同步失败')
    } finally {
      setRestoring(null)
    }
  }, [])

  const runConfirm = async () => {
    if (!confirm) return
    setConfirmBusy(true)
    try {
      await confirm.onConfirm()
    } catch (e) {
      setError(e instanceof Error ? e.message : '操作失败')
      setConfirm(null)
    } finally {
      setConfirmBusy(false)
    }
  }

  const pendingRequests = useMemo(
    () => requests.filter((q) => q.status === 'pending').length, [requests],
  )

  // ---------- 派生统计与可视化数据 ----------
  const runCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const status of RUN_ORDER) counts.set(status, 0)
    for (const r of runs) counts.set(r.status, (counts.get(r.status) ?? 0) + 1)
    return counts
  }, [runs])

  const totalFound = sum(runs.map((r) => r.items_found))
  const totalSaved = sum(runs.map((r) => r.items_saved))
  const hitRate = totalFound > 0 ? (totalSaved / totalFound) * 100 : null

  const activeCount = runs.filter((r) => ACTIVE_RUNS.includes(r.status as RunStatus)).length
  const successCount = runs.filter((r) => r.status === 'success').length

  const donutData: DonutSegment[] = RUN_ORDER.filter((s) => (runCounts.get(s) ?? 0) > 0).map((s) => ({
    label: RUN_STATUS[s].label,
    value: runCounts.get(s) ?? 0,
    color: RUN_STATUS[s].color,
  }))

  const funnel = useMemo(() => {
    const counts = new Map<string, number>()
    for (const c of contents) counts.set(c.review_status, (counts.get(c.review_status) ?? 0) + 1)
    return REVIEW_ORDER.map((s) => ({ status: s, count: counts.get(s) ?? 0 }))
      .filter((f) => f.count > 0)
  }, [contents])

  const hotRows = useMemo(() => {
    return contents
      .map((c) => ({ content: c, score: engagementScore(c) }))
      .sort((a, b) => b.score - a.score)
      .slice(0, 5)
      .map(({ content, score }) => ({
        label: content.title || content.text?.slice(0, 24) || '无标题内容',
        value: score,
        sub: content.author_name ? `@${content.author_name}` : platformLabel(content.platform),
      }))
  }, [contents])

  const reviewedCount = contents.filter((content) => content.review_status !== 'pending').length
  const analyzedCount = contents.filter((content) => (summaries[content.content_id]?.analysis_count ?? 0) > 0).length
  const reviewRate = contents.length > 0 ? (reviewedCount / contents.length) * 100 : 0
  const analysisRate = contents.length > 0 ? (analyzedCount / contents.length) * 100 : 0
  const interactionTotals = useMemo(() => {
    const totals = [0, 0, 0, 0]
    for (const content of contents) {
      const values = contentEngagementParts(content)
      values.forEach((value, index) => { totals[index] += value })
    }
    return totals
  }, [contents])
  const interactionTotal = sum(interactionTotals)
  const interactionPeak = Math.max(1, ...interactionTotals)

  const recentColumns = useMemo(() => {
    const byTime = [...runs].sort((a, b) =>
      String(a.started_at ?? a.finished_at ?? a.id).localeCompare(String(b.started_at ?? b.finished_at ?? b.id)),
    )
    return byTime.slice(-10).map((r) => ({
      label: `${r.target} · ${runMeta(r.status).label}`,
      found: r.items_found,
      saved: r.items_saved,
    }))
  }, [runs])

  const clock = lastUpdated ? lastUpdated.slice(11, 19) : ''

  return (
    <div className="app">
      <header className="page-header">
        <div className="brand">
          <p className="eyebrow">XHS-WORK PLANE</p>
          <h1>XHS作品内容与数据分析看板</h1>
          <p className="tagline">XHS采集 → 作品池 → 可信分析</p>
        </div>
        <div className="header-actions">
          {/* 文案只跟用户操作走；后台轮询只改呼吸点，避免每 2 秒换字导致的横向位移 */}
          <span className="header-chip">
            <span className={`dot ${submitting || syncing || activeCount > 0 ? 'live' : 'accent'}`} />
            {submitting ? '提交中…' : clock ? `更新 ${clock}` : '未同步'}
          </span>
          {/* 知识库入口独立成行，落在同步时间下方；书本图标 + 紫色渐变，跟主色蓝区分开 */}
          <button
            className="kb-entry"
            onClick={onNav}
            title="打开 RAG 知识库：检索已审核语料、审核入库草稿"
          >
            <svg className="kb-entry-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 7v14" />
              <path d="M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z" />
            </svg>
            <span className="kb-entry-label">RAG 知识库</span>
            <span className="kb-entry-arrow" aria-hidden="true">›</span>
          </button>
        </div>
      </header>

      {error && <div className="error-banner">{error}</div>}

      {/* 视觉锚点：净采集命中率 */}
      <section className="kpi-hero" aria-label="XHS采集与分析概览">
        <div className="hero-primary">
          <span className="kpi-label"><span className="dot live" />XHS采集命中率</span>
          <strong className="kpi-big tnum">{hitRate == null ? '—' : `${hitRate.toFixed(0)}%`}</strong>
          <p className="kpi-sub">
            保存 <b className="tnum">{totalSaved}</b> / 发现 <b className="tnum">{totalFound}</b> · 成功任务 <b className="tnum">{successCount}</b>
          </p>
          <div className="kpi-meter kpi-meter-blue"><i style={{ width: `${Math.min(100, hitRate ?? 0)}%` }} /></div>
        </div>
        <div className="kpi-card kpi-card-violet">
          <span className="kpi-label">XHS作品池</span>
          <strong className="kpi-big tnum">{contents.length}</strong>
          <p className="kpi-sub">已审核 <b className="tnum">{reviewedCount}</b> · 展示最近 {CONTENT_WINDOW} 条</p>
          <div className="kpi-meter kpi-meter-violet"><i style={{ width: `${reviewRate}%` }} /></div>
        </div>
        <div className="kpi-card kpi-card-amber">
          <span className="kpi-label"><span className="dot warn" />互动信号</span>
          <strong className="kpi-big tnum">{fmtCompact(interactionTotal)}</strong>
          <p className="kpi-sub">作品池累计 · 点赞 / 评论 / 转发 / 收藏</p>
          <div className="kpi-bars" aria-label="互动类型分布">
            {interactionTotals.map((value, index) => <i key={index} style={{ height: `${Math.max(8, (value / interactionPeak) * 100)}%` }} />)}
          </div>
        </div>
        <div className="kpi-card kpi-card-green">
          <span className="kpi-label"><span className="dot ok" />可信分析覆盖</span>
          <strong className="kpi-big tnum">{analysisRate.toFixed(0)}%</strong>
          <p className="kpi-sub">已分析 <b className="tnum">{analyzedCount}</b> / {contents.length || 0} 作品 · 已审核 <b className="tnum">{reviewedCount}</b></p>
          <div className="kpi-meter kpi-meter-green"><i style={{ width: `${analysisRate}%` }} /></div>
        </div>
      </section>

      <div className="layout">
        {/* ================= 主列 ================= */}
        <main className="col-main">
          {/* app 端只能「提交意图」（纯写库、零外呼），放行权在管理员这里 ——
              设备令牌编在 APK 里可反编译，这一层人工确认就是那条边界。 */}
          <section className="panel">
            <div className="panel-head">
              <h2>app 采集申请</h2>
              <span className="note">
                {pendingRequests > 0
                  ? `${pendingRequests} 条待放行 · 放行后才真正访问平台`
                  : '来自 app 端的采集意图，需在此放行'}
              </span>
            </div>
            {requests.length === 0 ? (
              <p className="empty">暂无 app 采集申请</p>
            ) : (
              <table className="run-table">
                <thead>
                  <tr>
                    <th>关键词</th>
                    <th>条数</th>
                    <th>提交时间</th>
                    <th>状态</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {requests.map((q) => {
                    const meta = REQUEST_STATUS[q.status]
                    const pending = q.status === 'pending'
                    return (
                      <tr key={q.id}>
                        <td>
                          <div className="run-target">
                            <b title={q.target}>{q.target}</b>
                            <span className="run-meta">
                              来自 {q.requested_by} · {TARGET_TYPE_LABEL[q.target_type] ?? q.target_type}
                              <span className="cat-tag tnum">{q.id.slice(0, 8)}</span>
                            </span>
                          </div>
                        </td>
                        <td className="tnum">{q.max_items}</td>
                        <td className="tnum faint">{stamp(q.created_at)}</td>
                        <td>
                          <span className="status-chip">
                            <span className="dot" style={{ background: meta.color }} />
                            <span style={{ color: meta.color }}>{meta.label}</span>
                          </span>
                          {q.note && <div className="faint" style={{ fontSize: 11, marginTop: 2 }}>{q.note}</div>}
                        </td>
                        <td onClick={(e) => e.stopPropagation()}>
                          <div style={{ display: 'flex', gap: 6, alignItems: 'center', justifyContent: 'flex-end' }}>
                            {pending ? (
                              <>
                                <button className="btn btn-sm btn-primary" onClick={() => decideRequest(q, 'approve')}>放行</button>
                                <button className="btn btn-sm btn-danger-ghost" onClick={() => decideRequest(q, 'reject')}>驳回</button>
                              </>
                            ) : q.run_id ? (
                              <button className="btn btn-sm" onClick={() => selectRun(q.run_id!)}>查看任务</button>
                            ) : (
                              <span className="faint">—</span>
                            )}
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>XHS采集任务</h2>
              <span className="note">点击行查看事件时间线</span>
            </div>
            {runs.length === 0 ? (
              <p className="empty">暂无 XHS 采集任务，右侧创建第一个任务</p>
            ) : (
              <table className="run-table">
                <thead>
                  <tr>
                    <th>目标</th>
                    <th>状态</th>
                    <th>命中 保存/发现</th>
                    <th>异常</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {runs.map((r) => {
                    const meta = runMeta(r.status)
                    const hit = r.items_found > 0 ? r.items_saved / r.items_found : 0
                    const isOpen = selected?.run.id === r.id
                    const active = r.status === 'running' || r.status === 'queued'
                    return (
                      <Fragment key={r.id}>
                        <tr className={isOpen ? 'selected' : ''} onClick={() => selectRun(r.id)}>
                          <td>
                            <div className="run-target">
                              <b title={r.target}>{r.target}</b>
                              <span className="run-meta">
                                {platformLabel(r.platform)} · {TARGET_TYPE_LABEL[r.target_type] ?? r.target_type}
                                <span className="cat-tag tnum">{r.id.slice(0, 8)}</span>
                              </span>
                            </div>
                          </td>
                          <td>
                            <span className="status-chip">
                              <span className="dot" style={{ background: meta.color, ...(active ? { animation: 'pulse 1.8s infinite' } : {}) }} />
                              <span style={{ color: meta.color }}>{meta.label}</span>
                            </span>
                          </td>
                          <td>
                            <div className="hit-box">
                              <div className="hit-line">
                                <span>保存 <b className="tnum">{r.items_saved}</b> / {r.items_found}</span>
                                <span>{r.items_found ? `${Math.round(hit * 100)}%` : '—'}</span>
                              </div>
                              <div className="hit-track">
                                <div className={r.items_found ? 'hit-fill' : 'hit-fill muted'} style={{ width: `${Math.max(hit * 100, 1)}%` }} />
                              </div>
                            </div>
                          </td>
                          <td>
                            {r.error_category
                              ? <span className="err-tag" title={r.error_code ?? ''}>{r.error_category}{r.error_code ? `/${r.error_code}` : ''}</span>
                              : <span className="faint">—</span>}
                          </td>
                          <td onClick={(e) => e.stopPropagation()}>
                            <div style={{ display: 'flex', gap: 6, alignItems: 'center', justifyContent: 'flex-end' }}>
                              {active && <button className="btn btn-sm btn-danger-ghost" onClick={() => cancel(r.id)}>取消</button>}
                              <span className={`faint ${isOpen ? 'rot' : ''}`} style={{ fontSize: 11 }}>▾</span>
                            </div>
                          </td>
                        </tr>
                        {isOpen && selected && (
                          <tr key={`${r.id}-detail`}>
                            <td colSpan={5} style={{ padding: '6px 0 14px' }}>
                              <div className="run-detail">
                                <div className="sub">
                                  <span>开始 <b className="tnum">{selected.run.started_at ? selected.run.started_at.slice(11, 19) : '—'}</b></span>
                                  <span>结束 <b className="tnum">{selected.run.finished_at ? selected.run.finished_at.slice(11, 19) : '—'}</b></span>
                                  <span>重试 <b className="tnum">{selected.run.retry_count}</b></span>
                                  <span>限速信号 <b className="tnum">{selected.run.rate_limit_signal}</b></span>
                                  <span>工具 <b className="tnum">{selected.run.tool_version ?? '—'}</b></span>
                                  <span>
                                    耗时 <b className="tnum">
                                      {selected.run.latency_ms == null
                                        ? '—'
                                        : `${(selected.run.latency_ms / 1000).toFixed(1)}s`}
                                    </b>
                                  </span>
                                </div>
                                <ul className="events">
                                  {selected.events.map((ev) => (
                                    <li key={`${ev.seq}-${ev.ts}`}>
                                      <span className="ts">{ev.ts.slice(11, 19)}</span>
                                      <span className={`lvl lvl-${ev.level}`}>{ev.category ?? 'evt'}</span>
                                      <span className={`lvl-${ev.level}`}>{ev.message}</span>
                                    </li>
                                  ))}
                                  {selected.events.length === 0 && <li>无事件</li>}
                                </ul>
                              </div>
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    )
                  })}
                </tbody>
              </table>
            )}
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>XHS作品内容池</h2>
              <div className="contents-head-actions">
                <span className="note tnum">{contents.length} 条</span>
                <button className="btn btn-sm btn-light-green" onClick={runBulkAnalysis} disabled={Boolean(batchBusy) || contents.length === 0}>
                  {batchBusy === 'analysis' ? '分析中…' : '一键分析'}
                </button>
                <button className="btn btn-sm btn-ghost" onClick={runBulkReview} disabled={Boolean(batchBusy) || contents.length === 0}>
                  {batchBusy === 'review' ? '审核中…' : '一键审核'}
                </button>
                <button className="btn btn-sm btn-primary" onClick={createReport} disabled={busy === 'report' || Boolean(batchBusy) || contents.length === 0}>
                  {busy === 'report' ? '生成中…' : '生成审核报告'}
                </button>
                <button
                  className={`btn btn-sm ${compareOpen ? 'btn-primary' : 'btn-ghost'}`}
                  onClick={() => setCompareOpen((prev) => !prev)}
                  disabled={contents.length < 2}
                  title="选择两条内容，比较数据表现、内容结构、分析观点与知识库证据"
                >
                  {compareOpen ? '收起对比' : '对比内容'}
                </button>
              </div>
            </div>

            {/* 审核状态构成条 */}
            {funnel.length > 0 && (
              <div className="funnel">
                <div className="funnel-track">
                  {funnel.map((f) => (
                    <div
                      key={f.status}
                      className="funnel-seg"
                      style={{ width: `${(f.count / contents.length) * 100}%`, background: REVIEW_META[f.status]?.color ?? '#8f9dad' }}
                      title={`${reviewLabel(f.status)} ${f.count}`}
                    />
                  ))}
                </div>
                <div className="funnel-legend">
                  {funnel.map((f) => (
                    <span className="lg-row" key={f.status}>
                      <span className="lg-dot" style={{ background: REVIEW_META[f.status]?.color ?? '#8f9dad' }} />
                      {reviewLabel(f.status)} <b className="tnum">{f.count}</b>
                    </span>
                  ))}
                </div>
              </div>
            )}

            {contents.length === 0 ? (
              <p className="empty">暂无标准化内容</p>
            ) : (
              <>
                {compareOpen && (
                  <ComparePanel
                    contents={contents}
                    compareIds={compareIds}
                    result={comparison}
                    busy={compareBusy}
                    kbBusy={kbBusy}
                    distillBusy={distillBusy}
                    message={compareMessage}
                    onChange={(slot, value) => { setComparison(null); setCompareIds((prev) => slot === 'a' ? [value, prev[1]] : [prev[0], value]) }}
                    onCompare={runComparison}
                    onClear={() => setComparison(null)}
                    onSaveText={saveComparisonText}
                    onDistillQa={distillComparisonToQa}
                    onOpenKb={onNav}
                  />
                )}
                <div className="content-list">
                {contents.map((content, i) => {
                  const id = content.content_id
                  const versions = analyses[id]
                  const contentReports = reports[id]
                  const review = reviews[id]
                  const summary = summaries[id]
                  const open = inline?.contentId === id ? inline.view : null
                  const focusValue = focusDraft[id] ?? ''
                  // 挑中的版本：用户没挑过 → 最新一版 / 生效批次
                  const pickedAnalysisId = analysisPick[id] ?? versions?.[0]?.analysis_id
                  const pickedAnalysis = versions?.find((a) => a.analysis_id === pickedAnalysisId) ?? versions?.[0]
                  const pickedBatchId = reviewPick[id] ?? review?.batch_id ?? undefined
                  return (
                    <Fragment key={id}>
                      <ContentCard
                        content={content}
                        cardIndex={i}
                        busy={busy}
                        analysisBusy={analysisBusy === id}
                        hasAnalysis={(summary?.analysis_count ?? versions?.length ?? 0) > 0}
                        hasReport={(summary?.report_count ?? contentReports?.length ?? 0) > 0}
                        reportBusy={batchBusy === `report:${id}`}
                        openView={open}
                        onView={openInline}
                        onReview={runReview}
                        onAnalyze={analyzeContent}
                        onReport={createContentReport}
                        onToggleFocus={toggleFocus}
                        onDelete={confirmDeleteContent}
                        restoring={restoring === id}
                        onRestore={restoreToApp}
                      />

                      {open && (
                        <div className="inline-detail">
                          {open === 'analysis' && (pickedAnalysis ? (
                            <AnalysisPanel
                              content={content}
                              versions={versions ?? []}
                              picked={pickedAnalysis}
                              onPick={(aid) => setAnalysisPick((prev) => ({ ...prev, [id]: aid }))}
                              savingKb={kbBusy}
                              distillingQa={distillBusy}
                              onDistillQa={distillAnalysisToQa}
                              onAskSave={(a) => setConfirm({
                                title: `把${versionLabel((versions ?? []).findIndex((x) => x.analysis_id === a.analysis_id), (versions ?? []).length)}保存到知识库？`,
                                message: '入库后该版本会成为可语义检索的 RAG 语料。同一内容的其他版本不受影响，你随时可以再入库另一版。',
                                items: [
                                  `来源：作品分析 ${a.analysis_id.slice(0, 8)}`,
                                  `判定：${ANALYSIS_VERDICT[a.verdict]?.label ?? a.verdict}`,
                                  a.in_kb ? '该版本已在知识库中（重复入库会覆盖同源文档）' : '该版本尚未入库',
                                ],
                                note: '入库是追加式操作，可随时重做，不会删除已有知识。',
                                confirmLabel: '确认入库',
                                onConfirm: () => saveAnalysisToKb(a),
                              })}
                              onClose={() => setInline(null)}
                              onNext={openInline}
                              onReport={createContentReport}
                            />
                          ) : <p className="empty">{panelBusy === `analysis:${id}` ? '加载中…' : '暂无分析结果，点「分析」生成'}</p>)}

                          {open === 'review' && (review ? (
                            <ReviewPanel
                              review={review}
                              batches={review.batches ?? []}
                              pickedBatchId={pickedBatchId}
                              busy={busy}
                              content={content}
                              onPickBatch={pickBatch}
                              onActivateBatch={activateBatch}
                              activating={busy === id}
                              onDecide={decide}
                              onClose={() => setInline(null)}
                              onReReview={runReview}
                              reReviewing={busy === id}
                              onNext={openInline}
                              onReport={createContentReport}
                            />
                          ) : <p className="empty">{panelBusy === `review:${id}` ? '加载中…' : '暂无审核结果'}</p>)}

                          {open === 'report' && (contentReports?.length ? (
                            <ReportPanel
                              reports={contentReports}
                              busy={batchBusy === `report:${id}`}
                              onRegenerate={createContentReport}
                              content={content}
                              onClose={() => setInline(null)}
                              onNext={openInline}
                            />
                          ) : (
                            <div className="inline-empty">
                              <p className="empty">
                                {panelBusy === `report:${id}` ? '加载中…' : '该内容还没有单篇报告'}
                              </p>
                              <button className="btn btn-sm btn-primary" onClick={() => createContentReport(content)}>
                                生成单篇报告
                              </button>
                            </div>
                          ))}
                        </div>
                      )}

                      {/* 重新分析的角度注入：主体分析方向不变，只在既有框架内补充观察侧重 */}
                      {focusOpen === id && (
                        <div className="focus-inject">
                          <label className="field-label" htmlFor={`focus-${id}`}>
                            补充关注点（可选）· 仅在标准分析框架内补充观察侧重，不改变判定口径
                          </label>
                          <textarea
                            id={`focus-${id}`}
                            className="field focus-input"
                            rows={2}
                            maxLength={500}
                            placeholder="例如：重点看开头 3 秒的钩子设计；对比同期同话题账号的人设差异"
                            value={focusValue}
                            onChange={(e) => setFocusDraft((prev) => ({ ...prev, [id]: e.target.value }))}
                          />
                          <div className="focus-actions">
                            <span className="note tnum">{focusValue.length}/500</span>
                            <button
                              className="btn btn-sm btn-primary"
                              disabled={analysisBusy === id}
                              onClick={() => analyzeContent(content, focusValue.trim())}
                            >
                              {analysisBusy === id ? '分析中…' : focusValue.trim() ? '按补充视角分析' : '标准重新分析'}
                            </button>
                            <button className="btn btn-sm btn-ghost" onClick={() => setFocusOpen(null)}>取消</button>
                          </div>
                        </div>
                      )}
                    </Fragment>
                  )
                })}
                </div>
              </>
            )}
          </section>

          {/* 报告预览（跨内容报告，仍留在列表下方） */}
          {report && (
            <section className="panel">
              <div className="panel-head">
                <h2>{report.title}</h2>
                <div className="contents-head-actions">
                  <span className="note tnum">{report.created_at.slice(0, 16).replace('T', ' ')}</span>
                  <button className="btn btn-sm btn-ghost" onClick={() => setReport(null)}>关闭</button>
                </div>
              </div>
              <div className="report-scroll"><pre className="report-preview">{report.markdown}</pre></div>
            </section>
          )}
        </main>

        {/* ================= 侧列 ================= */}
        <aside className="col-side">
          <div className="side-float" aria-label="侧栏控制窗口">
          <BrowserPanel />

          <section className="panel">
            <div className="panel-head"><h2>新建 XHS 采集任务</h2></div>
            <form
              className="form-grid"
              onSubmit={(e) => { e.preventDefault(); create() }}
            >
              <div>
                <label className="field-label" htmlFor="run-type">采集方式</label>
                <select id="run-type" className="field" value={targetType} onChange={(e) => setTargetType(e.target.value as TargetType)}>
                  <option value="keyword">关键词搜索</option>
                  <option value="url">笔记 URL 详情</option>
                  <option value="account">账号内容</option>
                  <option value="post">笔记</option>
                </select>
              </div>
              <div>
                <label className="field-label" htmlFor="run-target">目标</label>
                <input
                  id="run-target"
                  className="field"
                  placeholder={targetType === 'keyword' ? '关键词，如 AI眼镜' : '目标 URL / 账号'}
                  value={target}
                  onChange={(e) => setTarget(e.target.value)}
                />
              </div>
              <div>
                <label className="field-label" htmlFor="run-max">条数上限</label>
                <input
                  id="run-max"
                  className="field"
                  type="number"
                  min={1}
                  max={100}
                  value={maxItems}
                  onChange={(e) => setMaxItems(Number(e.target.value))}
                />
              </div>
              {/* 固定宽度：标签切换不再带动表单重排（文字闪回的观感一半来自这里） */}
              <button type="submit" className="btn btn-primary create-submit" disabled={!target.trim() || submitting}>
                {submitting ? '创建中…' : '创建任务'}
              </button>
            </form>
          </section>

          <section className="panel">
            <div className="panel-head"><h2>任务状态构成</h2><span className="note tnum">{runs.length} 任务</span></div>
            {runs.length === 0 ? (
              <p className="empty">暂无数据</p>
            ) : (
              <div className="status-flex">
                <Donut data={donutData} centerValue={fmtCount(runs.length)} centerCaption="任务总数" />
                <div className="donut-legend">
                  {RUN_ORDER
                    .filter((s) => (runCounts.get(s) ?? 0) > 0)
                    .map((s) => {
                      const meta = RUN_STATUS[s]
                      const value = runCounts.get(s) ?? 0
                      return (
                        <span className="lg-row" key={s}>
                          <span className="lg-dot" style={{ background: meta.color }} />
                          <span className="lg-name">{meta.label}</span>
                          <span className="lg-val tnum">{value}</span>
                          <span className="lg-pct tnum">{Math.round((value / runs.length) * 100)}%</span>
                        </span>
                      )
                    })}
                </div>
              </div>
            )}
          </section>

          <section className="panel">
            <div className="panel-head"><h2>采集命中走势</h2><span className="note">最近 {Math.min(10, runs.length)} 次任务</span></div>
            {runs.length === 0 ? (
              <p className="empty">暂无数据</p>
            ) : (
              <>
                <MiniColumns data={recentColumns} />
                <div className="col-legend">
                  <span><i className="found" />发现</span>
                  <span><i className="saved" />保存</span>
                </div>
              </>
            )}
          </section>

          <section className="panel">
            <div className="panel-head"><h2>XHS互动最高作品</h2><span className="note">点赞+评论+转发+收藏</span></div>
            <RankBars rows={hotRows} formatValue={fmtCompact} />
          </section>
          </div>
        </aside>
      </div>
      <ConfirmDialog
        open={confirm !== null}
        title={confirm?.title ?? ''}
        message={confirm?.message}
        items={confirm?.items}
        note={confirm?.note}
        confirmLabel={confirm?.confirmLabel}
        danger={confirm?.danger}
        busy={confirmBusy}
        onConfirm={runConfirm}
        onCancel={() => setConfirm(null)}
      />
    </div>
  )
}

function ComparePanel({ contents, compareIds, result, busy, kbBusy, distillBusy, message, onChange, onCompare, onClear, onSaveText, onDistillQa, onOpenKb }: {
  contents: Content[]
  compareIds: [string, string]
  result: ComparisonResult | null
  busy: boolean
  kbBusy: boolean
  distillBusy: boolean
  message: string | null
  onChange: (slot: 'a' | 'b', value: string) => void
  onCompare: () => void
  onClear: () => void
  onSaveText: (result: ComparisonResult) => void
  onDistillQa: (result: ComparisonResult) => void
  onOpenKb: () => void
}) {
  const a = result?.a ?? contents.find((c) => c.content_id === compareIds[0])
  const b = result?.b ?? contents.find((c) => c.content_id === compareIds[1])
  const aAnalysis = result?.analyses[0] ?? null
  const bAnalysis = result?.analyses[1] ?? null
  const aScore = a ? engagementScore(a) : 0
  const bScore = b ? engagementScore(b) : 0
  const stronger = aScore === bScore ? '两条内容当前快照总互动相同' : aScore > bScore ? '内容 A 当前快照更强' : '内容 B 当前快照更强'
  const reasons = (analysis: Analysis | null) => {
    if (!analysis) return []
    const payload = analysis.payload
    const source = analysis.verdict === 'viral' ? payload.viral_reasons : payload.flat_reasons
    return source.slice(0, 4)
  }
  const suggestions = [...(aAnalysis?.payload.suggestions ?? []), ...(bAnalysis?.payload.suggestions ?? [])]
    .filter(Boolean)
    .filter((item, index, items) => items.indexOf(item) === index)
    .slice(0, 6)
  const winnerFor = (winner: ComparisonDimension['winner']) => winner === 'a' ? 'A' : winner === 'b' ? 'B' : winner === 'tie' ? '接近' : '—'
  const verdict = (analysis: Analysis | null) => analysis ? (ANALYSIS_VERDICT[analysis.verdict]?.label ?? analysis.verdict) : '未生成分析'

  return (
    <section className="compare-panel" aria-label="双内容对比分析">
      <div className="compare-head">
        <div>
          <p className="compare-kicker">COMPARE / RAG EVIDENCE</p>
          <h3>双内容对比分析</h3>
          <p className="compare-desc">用同一组真实快照比较表现，再用已入库知识检索案例证明或反驳观点。</p>
        </div>
        {result && <span className="compare-generated">生成于 {result.createdAt.slice(11, 16).replace('T', ' ')}</span>}
      </div>

      <div className="compare-selectors">
        <label className="compare-select compare-select-a">
          <span><i className="compare-mark">A</i> 内容 A</span>
          <select className="field" value={compareIds[0]} onChange={(e) => onChange('a', e.target.value)}>
            <option value="">选择内容 A</option>
            {contents.map((content) => <option key={content.content_id} value={content.content_id} disabled={content.content_id === compareIds[1]}>{contentLabel(content)}</option>)}
          </select>
        </label>
        <span className="compare-vs">VS</span>
        <label className="compare-select compare-select-b">
          <span><i className="compare-mark">B</i> 内容 B</span>
          <select className="field" value={compareIds[1]} onChange={(e) => onChange('b', e.target.value)}>
            <option value="">选择内容 B</option>
            {contents.map((content) => <option key={content.content_id} value={content.content_id} disabled={content.content_id === compareIds[0]}>{contentLabel(content)}</option>)}
          </select>
        </label>
        <button className="btn btn-primary compare-submit" disabled={busy || !a || !b || a.content_id === b.content_id} onClick={onCompare}>
          {busy ? '分析与检索中…' : '生成对比分析'}
        </button>
        {result && <button className="btn btn-ghost" onClick={onClear}>清空结果</button>}
      </div>

      {busy && <div className="compare-progress"><span className="dot live" />正在串行补齐单条分析，并检索 RAG 证据；已有分析会直接复用。</div>}

      {result && a && b && (
        <div className="compare-result">
          <div className="compare-summary">
            <div className="compare-summary-main">
              <span className="compare-summary-label">快照结论</span>
              <strong>{stronger}</strong>
              <p>这是互动数据的相对结果，不等同于平台曝光或因果结论。</p>
              <p>采集时间：A {a.collected_at.slice(0, 16).replace('T', ' ')} · B {b.collected_at.slice(0, 16).replace('T', ' ')}</p>
              <p>可信分层：指标=标准化快照；分析=模型观点；RAG=引用证据。</p>
            </div>
            <div className="compare-score"><span>A 总互动</span><b>{fmtCompact(aScore)}</b></div>
            <div className="compare-score"><span>B 总互动</span><b>{fmtCompact(bScore)}</b></div>
            <div className="compare-score"><span>RAG 命中</span><b>{result.kb.retrieval_count}</b></div>
          </div>

          <section className="compare-section">
            <div className="compare-section-head"><h4>一、真实数据多维对比</h4><span>来源：内容池当前快照</span></div>
            <div className="compare-table-wrap">
              <table className="compare-table">
                <thead><tr><th>维度</th><th className="compare-col-a">内容 A</th><th className="compare-col-b">内容 B</th><th>相对表现</th></tr></thead>
                <tbody>{result.dimensions.map((dimension) => (
                  <tr key={dimension.label}>
                    <td><b>{dimension.label}</b><small>{dimension.note}</small></td>
                    <td className={dimension.winner === 'a' ? 'compare-win' : ''}>{dimension.a}</td>
                    <td className={dimension.winner === 'b' ? 'compare-win' : ''}>{dimension.b}</td>
                    <td><span className={`compare-winner winner-${dimension.winner}`}>{winnerFor(dimension.winner)}</span></td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </section>

          <section className="compare-section">
            <div className="compare-section-head"><h4>二、内容机制与分析观点</h4><span>来源：作品分析 JSON；观点需结合证据复核</span></div>
            <div className="compare-insight-grid">
              {[['A', a, aAnalysis, reasons(aAnalysis)], ['B', b, bAnalysis, reasons(bAnalysis)]].map(([slot, content, analysis, items]) => {
                const current = analysis as Analysis | null
                const currentItems = items as AnalysisReason[]
                return (
                  <article className={`compare-insight compare-insight-${String(slot).toLowerCase()}`} key={String(slot)}>
                    <div className="compare-insight-title"><i className="compare-mark">{String(slot)}</i><b>{contentLabel(content as Content)}</b><span className="chip">{verdict(current)}</span></div>
                    <p className="compare-source-id">{(content as Content).platform} · {(content as Content).content_id}</p>
                    {current?.payload.summary && <p className="compare-analysis-summary">{current.payload.summary}</p>}
                    {currentItems.length > 0 ? currentItems.map((reason) => (
                      <div className="compare-reason" key={`${String(slot)}-${reason.factor}`}>
                        <b>{reason.factor}</b><p>{reason.evidence}</p><span>置信度 {Math.round(reason.confidence * 100)}%</span>
                      </div>
                    )) : <p className="empty left">暂无已生成的机制观点；先查看上方真实指标。</p>}
                  </article>
                )
              })}
            </div>
          </section>

          <section className="compare-section">
            <div className="compare-section-head"><h4>三、RAG 案例证据</h4><span>只展示本次检索返回的引用</span></div>
            {result.kb.answer && <p className="compare-rag-answer">{result.kb.answer}</p>}
            {result.kb.citations.length > 0 ? (
              <div className="compare-citations">{result.kb.citations.map((citation) => (
                <article className="compare-citation" key={`${citation.chunk_id}-${citation.doc_id}`}>
                  <div><b>{citation.title}</b><span className={citation.verified ? 'citation-verified' : 'citation-unverified'}>{citation.verified ? '已审核' : '待审核'}</span><span className="citation-score">匹配 {Math.round(citation.score * 100)}%</span></div>
                  <p>{citation.text}</p>
                  <small>来源文档：{citation.doc_id}{citation.question ? ` · Q：${citation.question}` : ''}</small>
                </article>
              ))}</div>
            ) : <p className="empty left">本次没有检索到可引用案例，不能用知识库证明观点。</p>}
            {result.kb.limitations.length > 0 && <p className="compare-limitations">检索限制：{result.kb.limitations.join('；')}</p>}
          </section>

          <section className="compare-section compare-advice">
            <div className="compare-section-head"><h4>四、下一步建议</h4><span>建议不是事实结论</span></div>
            {suggestions.length > 0 ? <ol>{suggestions.map((suggestion) => <li key={suggestion}>{suggestion}</li>)}</ol> : <p className="empty left">暂无模型建议；优先围绕总互动、深度互动占比和转发/收藏差距做 A/B 测试。</p>}
            <p className="compare-advice-note">建议先复刻表现更强内容的开头承诺与价值表达，再单独测试标题、首屏信息密度和行动引导；每次只改一个变量。</p>
          </section>

          <section className="compare-section compare-export">
            <div className="compare-section-head"><h4>五、保存为知识</h4><span>沿用普通内容的知识库链路</span></div>
            <div className="compare-export-actions">
              <button className="btn btn-light-green" disabled={kbBusy || distillBusy} onClick={() => onSaveText(result)}>
                {kbBusy ? '文本入库中…' : '文本直接入库'}
              </button>
              <button className="btn btn-ghost" disabled={kbBusy || distillBusy} onClick={() => onDistillQa(result)}>
                {distillBusy ? 'Q&A 蒸馏中…' : '蒸馏为 Q&A 草稿'}
              </button>
              <button className="btn btn-ghost" onClick={onOpenKb}>打开知识库审核</button>
            </div>
            {message && <p className="compare-message">{message}</p>}
          </section>
        </div>
      )}
    </section>
  )
}

/* ---------- 下一步引导 ---------- */
/** 结果产出后的动作引导条：把「看完能干什么」直接摆出来，而不是让用户自己找按钮。 */
function NextSteps({ title = '下一步', steps }: { title?: string; steps: NextStep[] }) {
  return (
    <div className="next-steps">
      <span className="next-label">{title}</span>
      {steps.map((s) => (
        <button
          key={s.label}
          className={`next-chip${s.done ? ' done' : ''}`}
          onClick={s.onClick}
          title={s.hint}
          disabled={s.done}
        >
          {s.done && <span aria-hidden="true">✓</span>}
          {s.label}
          <i className="next-hint">{s.hint}</i>
        </button>
      ))}
    </div>
  )
}

/* ---------- 作品分析面板 ---------- */
/** 单条作品分析结果：版本条 + 判定 chip + 摘要 + 可审计 Markdown + 按版本入库。 */
const AnalysisPanel = memo(function AnalysisPanel({ content, versions, picked, onPick, savingKb, distillingQa, onAskSave, onDistillQa, onClose, onNext, onReport }: {
  content: Content
  versions: Analysis[]
  picked: Analysis
  onPick: (analysisId: string) => void
  savingKb: boolean
  distillingQa: boolean
  onAskSave: (a: Analysis) => void
  onDistillQa: (a: Analysis) => Promise<void>
  onClose: () => void
  onNext: (c: Content, view: InlineView) => void
  onReport: (c: Content) => void
}) {
  const [distilled, setDistilled] = useState(false)
  const pickedIndex = Math.max(versions.findIndex((a) => a.analysis_id === picked.analysis_id), 0)
  const pickedLabel = versionLabel(pickedIndex, versions.length)
  const options: VersionOption[] = versions.map((a, i) => ({
    id: a.analysis_id,
    label: versionLabel(i, versions.length),
    sublabel: stamp(a.created_at),
    badge: [i === 0 ? '最新' : '', a.in_kb ? '✓已入库' : ''].filter(Boolean).join(' ') || undefined,
  }))
  const p = picked.payload
  const v = ANALYSIS_VERDICT[picked.verdict] ?? ANALYSIS_VERDICT.uncertain
  const confidence = p.confidence == null ? null : Math.round(p.confidence * 100)
  const compared = p.comparison?.baseline_count ?? 0
  const related = picked.compared_with ?? []
  const distill = () => {
    setDistilled(true)
    onDistillQa(picked).catch(() => setDistilled(false))
  }
  const focus = (picked.focus || '').trim()
  return (
    <section className="panel inline-panel">
      <div className="panel-head">
        <h2>作品分析</h2>
        <div className="contents-head-actions">
          {focus && (
            <span className="chip" style={chipStyle('#7c3aed')} title={focus}>
              <span className="dot" style={{ background: '#7c3aed' }} />
              补充视角
            </span>
          )}
          <span className="chip" style={chipStyle(v.color)}>
            <span className="dot" style={{ background: v.color }} />
            {v.label}{confidence != null ? ` · 置信 ${confidence}%` : ''}
          </span>
          {picked.in_kb ? (
            <span className="chip" style={chipStyle('#16a34a')}>
              <span className="dot" style={{ background: '#16a34a' }} />
              该版本已入库
            </span>
          ) : (
            <button
              className="btn btn-sm btn-primary"
              disabled={savingKb}
              onClick={() => onAskSave(picked)}
              title="分块 + 向量化，写入 RAG 知识库（会二次确认版本）"
            >
              {savingKb ? '入库中…' : `保存${pickedLabel}到知识库`}
            </button>
          )}
          {distilled ? (
            <span className="chip" style={chipStyle('#7c3aed')}>
              <span className="dot" style={{ background: '#7c3aed' }} />
              已蒸馏 Q&A 草稿
            </span>
          ) : (
            <button className="btn btn-sm" disabled={distillingQa} onClick={distill} title="把这一版分析蒸馏成问答知识草稿，审核通过后入库">
              {distillingQa ? '蒸馏中…' : '蒸馏为 Q&A 知识'}
            </button>
          )}
          <button className="btn btn-sm btn-ghost" onClick={onClose}>关闭</button>
        </div>
      </div>

      {/* 多版本警示：重析是新增一版，不是覆盖；入库只作用于当前挑中的那一版 */}
      <VersionBar
        versions={options}
        selectedId={picked.analysis_id}
        onSelect={onPick}
        latestId={versions[0]?.analysis_id}
        warnHint="下面的「保存到知识库」「蒸馏 Q&A」都只作用于这一版。"
        emptyHint="暂无分析结果"
      />

      {p.summary && <p className="analysis-lead">{p.summary}</p>}
      <div className="analysis-meta">
        {p.topic && <span>话题 <b>{p.topic}</b></span>}
        <span>同话题基线 <b className="tnum">{compared}</b> 条</span>
        {related.length > 0 && (
          <span className="faint">
            对比{' '}
            {related.slice(0, 3).map((id) => (
              <code className="tnum" key={id}>{id}</code>
            ))}
          </span>
        )}
        {related.length === 0 && <span className="faint">单条深度分析</span>}
      </div>
      {focus && <p className="analysis-focus">补充关注点：{focus}</p>}
      <div className="report-scroll"><pre className="report-preview">{picked.markdown}</pre></div>
      <NextSteps
        steps={[
          {
            label: picked.in_kb ? '该版本已入库' : `保存${pickedLabel}到知识库`,
            hint: '分块 + 向量化，成为可语义检索的 RAG 语料（入库前会二次确认）',
            done: picked.in_kb, onClick: () => onAskSave(picked),
          },
          {
            label: distilled ? '已蒸馏 Q&A' : `蒸馏${pickedLabel}为 Q&A`,
            hint: '拆成「问题+答案」知识条目，审核通过后入库',
            done: distilled, onClick: distill,
          },
          { label: '查看审核断言', hint: '看这条内容有哪些断言被本地语料支持或反驳', onClick: () => onNext(content, 'review') },
          { label: '生成单篇报告', hint: '产出可审计的 JSON + Markdown 审核报告', onClick: () => onReport(content) },
        ]}
      />
    </section>
  )
})

/* ---------- 单篇报告面板 ---------- */
/** 单篇报告结果：版本条挑版本 + 预览 + 重新生成（新版追加，旧版保留）。 */
const ReportPanel = memo(function ReportPanel({ content, reports, busy, onRegenerate, onClose, onNext }: {
  content: Content
  reports: Report[]
  busy: boolean
  onRegenerate: (c: Content, force?: boolean) => void
  onClose: () => void
  onNext: (c: Content, view: InlineView) => void
}) {
  const [pickedId, setPickedId] = useState(reports[0]?.report_id)
  const current = reports.find((r) => r.report_id === pickedId) ?? reports[0]
  const [copied, setCopied] = useState(false)
  if (!current) return null
  const currentIndex = Math.max(reports.findIndex((r) => r.report_id === current.report_id), 0)
  const currentLabel = versionLabel(currentIndex, reports.length)
  const options: VersionOption[] = reports.map((r, i) => ({
    id: r.report_id,
    label: versionLabel(i, reports.length),
    sublabel: stamp(r.created_at),
    badge: i === 0 ? '最新' : undefined,
  }))
  const copy = () => {
    navigator.clipboard?.writeText(current.markdown)
      .then(() => { setCopied(true); setTimeout(() => setCopied(false), 1600) })
      .catch(() => setCopied(false))
  }
  return (
    <section className="panel inline-panel">
      <div className="panel-head">
        <h2>{current.title}</h2>
        <div className="contents-head-actions">
          <button className="btn btn-sm" disabled={busy} onClick={() => onRegenerate(content, true)}>
            {busy ? '生成中…' : '重新生成'}
          </button>
          <button className="btn btn-sm btn-ghost" onClick={onClose}>收起</button>
        </div>
      </div>

      <VersionBar
        versions={options}
        selectedId={current.report_id}
        onSelect={setPickedId}
        latestId={reports[0]?.report_id}
        warnHint="重新生成只会追加新的一版，旧版不会被覆盖。"
        emptyHint="暂无报告"
      />

      <div className="report-scroll"><pre className="report-preview">{current.markdown}</pre></div>
      <NextSteps
        steps={[
          { label: copied ? '已复制' : '复制 Markdown', hint: '整篇报告原文，便于外发存档', done: copied, onClick: copy },
          { label: '查看作品分析', hint: '这条内容为什么爆／为什么平淡的归因', onClick: () => onNext(content, 'analysis') },
          { label: '查看审核断言', hint: '报告结论对应的断言与证据', onClick: () => onNext(content, 'review') },
          { label: `重新生成（当前${currentLabel}）`, hint: '按当前数据再跑一版，旧版保留可回看', onClick: () => onRegenerate(content, true) },
        ]}
      />
    </section>
  )
})

/* ---------- 断言与证据子面板 ---------- */
/** 内容池卡片：封面缩略图 + 8 字段 + 678 互动动画 + 分析/审核/报告/删除入口。 */
const ContentCard = memo(function ContentCard({ content, cardIndex, busy, analysisBusy, hasAnalysis, hasReport, reportBusy, openView, onView, onReview, onAnalyze, onReport, onToggleFocus, onDelete, restoring, onRestore }: {
  content: Content
  /** 入场动画的错峰序号（封顶在 CSS 侧处理） */
  cardIndex: number
  busy: string | null
  analysisBusy: boolean
  hasAnalysis: boolean
  hasReport: boolean
  reportBusy: boolean
  openView: InlineView | null
  onView: (c: Content, view: InlineView) => void
  onReview: (c: Content, force?: boolean) => void
  onAnalyze: (c: Content, focus?: string) => void
  onReport: (c: Content, force?: boolean) => void
  onToggleFocus: (c: Content) => void
  onDelete: (c: Content) => void
  restoring: boolean
  onRestore: (c: Content) => void
}) {
  // 已审核过（非 pending）才给「查看审核」；未审核时给一次性「自动审核」
  const reviewed = content.review_status !== 'pending'
  // app 端「删除」= 软隐藏：这条在 app 上不可见，但在 web 看板里**照常存在**
  const appHidden = Boolean(content.app_hidden_at)
  const e = content.engagement ?? {}
  const tags = content.tags ?? []
  const typeText = content.content_type ? (CONTENT_TYPE_LABEL[content.content_type] ?? content.content_type) : null
  // 内容池图片落库后优先展示本地 /media/...（不随 xsec_token 过期），远程 URL 作回退
  const hasCover = Boolean(content.cover_local || content.cover_url)
  // 四维互动任一有值即渲染四连条（赞/评/转/藏）；全空才退化为占位
  const hasEng = (['likes', 'comments', 'shares', 'collects'] as const)
    .some((k) => e[k] != null && toNum(e[k]) > 0)

  /** 封面加载失败：先回退远程 URL，再无则隐藏该图。 */
  const onCoverError = (ev: SyntheticEvent<HTMLImageElement>) => {
    const img = ev.currentTarget
    if (!img.dataset.fb && content.cover_local && content.cover_url) {
      img.dataset.fb = '1'
      img.src = content.cover_url
      return
    }
    img.style.visibility = 'hidden'
  }

  return (
    <article
      className={`content-item${hasCover ? ' has-cover' : ''}`}
      style={{ '--card-i': Math.min(cardIndex, 12) } as CSSProperties}
    >
      {hasCover && (
        <a
          className="content-cover"
          href={content.canonical_url ?? undefined}
          target="_blank"
          rel="noreferrer"
          onClick={(ev) => ev.stopPropagation()}
          title="悬停看大图 · 点击打开原帖"
        >
          <img
            className="cover-thumb"
            src={content.cover_local ?? content.cover_url ?? ''}
            alt=""
            loading="lazy"
            referrerPolicy="no-referrer"
            onError={onCoverError}
          />
          <span className="cover-zoom" aria-hidden="true">
            <img
              src={content.cover_local ?? content.cover_url ?? ''}
              alt=""
              loading="lazy"
              referrerPolicy="no-referrer"
              onError={onCoverError}
            />
            <i className="cover-zoom-cap">{content.title || content.author_name || '原帖'}</i>
          </span>
        </a>
      )}
      <div className="content-main">
        <div className="content-head">
          {typeText && <span className="type-badge">{typeText}</span>}
          <p className="content-title">{content.title || '无标题内容'}</p>
        </div>
        {content.text && <p className="content-snippet">{content.text}</p>}
        <div className="content-sub">
          <span>{platformLabel(content.platform)}</span>
          {content.author_name && <span>@{content.author_name}</span>}
          <span className="tnum">{content.published_at?.slice(0, 10) ?? ''}</span>
        </div>
        {tags.length > 0 && (
          <div className="tag-row">
            {tags.slice(0, 6).map((t) => (
              <span className="tag-chip" key={t}>#{t}</span>
            ))}
          </div>
        )}
        {hasEng ? (
          <EngagementBar engagement={content.engagement} />
        ) : (
          <p className="content-eng-fallback tnum">暂无互动数据</p>
        )}
      </div>
      <div className="content-actions">
        <span className="chip" style={chipStyle(reviewColor(content.review_status))}>
          <span className="dot" style={{ background: reviewColor(content.review_status) }} />
          {reviewLabel(content.review_status)}
        </span>

        {/* app 端隐藏标记：提醒管理员这条在手机上已经看不到了，需要时点下面恢复 */}
        {appHidden && (
          <span
            className="chip"
            style={chipStyle('#8b8b8b')}
            title={`app 端已隐藏 · ${content.app_hidden_at?.slice(0, 19).replace('T', ' ') ?? ''}`}
          >
            <span className="dot" style={{ background: '#8b8b8b' }} />
            app 端已隐藏
          </span>
        )}

        {/* 分析：已分析过 → 查看 / 重析（可注入补充视角）；否则一次性分析 */}
        <div className="act-group">
          {hasAnalysis ? (
            <>
              <button
                className={`btn btn-sm btn-light-green${openView === 'analysis' ? ' active' : ''}`}
                onClick={() => onView(content, 'analysis')}
                title="查看全部历史分析版本，可任选一版入库"
              >
                查看分析
              </button>
              <button
                className="btn btn-sm btn-ghost btn-icon"
                disabled={analysisBusy}
                onClick={() => onToggleFocus(content)}
                title="重新分析：新增一版，旧版保留；可补充关注视角"
              >
                {analysisBusy ? '…' : '重析 ↻'}
              </button>
            </>
          ) : (
            <button
              className="btn btn-sm btn-primary"
              disabled={analysisBusy}
              onClick={() => onAnalyze(content)}
              title="用大模型分析该作品为何爆火或表现平淡"
            >
              {analysisBusy ? '分析中…' : '分析'}
            </button>
          )}
        </div>

        {/* 审核：已审核过 → 查看 / 重审；否则一次性自动审核 */}
        <div className="act-group">
          {reviewed ? (
            <>
              <button
                className={`btn btn-sm${openView === 'review' ? ' btn-light-green active' : ' btn-ghost'}`}
                onClick={() => onView(content, 'review')}
                title="查看断言、证据与全部历史审核批次"
              >
                查看审核
              </button>
              <button
                className="btn btn-sm btn-ghost btn-icon"
                disabled={busy === content.content_id}
                onClick={() => onReview(content, true)}
                title="重新审核：新增一个审核批次，旧批次完整保留"
              >
                {busy === content.content_id ? '…' : '重审 ↻'}
              </button>
            </>
          ) : (
            <button
              className="btn btn-sm btn-ghost"
              disabled={busy === content.content_id}
              onClick={() => onReview(content)}
            >
              {busy === content.content_id ? '审核中…' : '自动审核'}
            </button>
          )}
        </div>

        {/* 报告：已有报告 → 查看 / 重新生成；否则生成第一篇 */}
        <div className="act-group">
          {hasReport ? (
            <>
              <button
                className={`btn btn-sm${openView === 'report' ? ' btn-light-green active' : ' btn-ghost'}`}
                onClick={() => onView(content, 'report')}
                title="查看该内容的单篇报告（含历史版本）"
              >
                查看报告
              </button>
              <button
                className="btn btn-sm btn-ghost btn-icon"
                disabled={reportBusy}
                onClick={() => onReport(content, true)}
                title="重新生成一版报告，旧版保留"
              >
                {reportBusy ? '…' : '重报 ↻'}
              </button>
            </>
          ) : (
            <button
              className="btn btn-sm btn-ghost"
              disabled={reportBusy}
              onClick={() => onReport(content)}
            >
              {reportBusy ? '报告中…' : '单篇报告'}
            </button>
          )}
        </div>

        {content.canonical_url && (
          <a
            className="btn btn-sm btn-ghost"
            href={content.canonical_url}
            target="_blank"
            rel="noreferrer"
            title="在新标签页打开原帖"
          >
            跳转原帖 ↗
          </a>
        )}

        {/* 「重新同步到 app 端」：只在已隐藏时出现。可逆，故不弹确认 */}
        {appHidden && (
          <button
            className="btn btn-sm btn-light-green"
            disabled={restoring}
            onClick={() => onRestore(content)}
            title="取消隐藏，app 下次拉取即恢复显示。内容与断言/分析/报告/KB 文档一直都在，无需重建"
          >
            {restoring ? '同步中…' : '重新同步到 app 端'}
          </button>
        )}

        {/* 内容池删除：红色，不可逆，点击后弹影响面确认 */}
        <button
          className="btn btn-sm btn-danger-ghost btn-del"
          onClick={() => onDelete(content)}
          title="从内容池永久删除这条内容及其断言/分析/报告/知识库文档"
        >
          删除
        </button>
      </div>
    </article>
  )
})

const ReviewPanel = memo(function ReviewPanel({ review, batches, pickedBatchId, content, busy, activating, onPickBatch, onActivateBatch, onDecide, onClose, onReReview, reReviewing, onNext, onReport }: {
  review: ContentReviewDetail
  batches: ReviewBatch[]
  pickedBatchId?: string
  content: Content
  busy: string | null
  activating: boolean
  onPickBatch: (c: Content, batchId: string) => void
  onActivateBatch: (contentId: string, batchId: string) => void
  onDecide: (claimId: string, status: ClaimStatus) => void
  onClose: () => void
  onReReview: (c: Content, force?: boolean) => void
  reReviewing: boolean
  onNext: (c: Content, view: InlineView) => void
  onReport: (c: Content) => void
}) {
  const manual = review.claims.filter(({ decisions }) => decisions.some((d) => d.reviewer === 'human')).length
  const activeId = batches.find((b) => b.active)?.batch_id
  const options: VersionOption[] = batches.map((b) => ({
    id: b.batch_id,
    label: `第 ${b.batch_seq} 版`,
    sublabel: stamp(b.created_at),
    badge: [b.active ? '生效' : '', `${b.claim_count} 条`].filter(Boolean).join(' '),
  }))
  // 挑中的批次还没落到详情里（例如刚点开会话）就先按生效批次展示，避免面板空白
  const shownBatchId = pickedBatchId ?? review.batch_id ?? activeId
  return (
    <div className="review-panel inline-panel">
      <div className="review-head">
        <h3>断言与证据</h3>
        <div className="contents-head-actions">
          <button
            className="btn btn-sm"
            disabled={reReviewing}
            onClick={() => onReReview(content, true)}
            title="重跑抽取与判定，结果作为**新的一版**追加，旧版完整保留"
          >
            {reReviewing ? '重审中…' : '重新审核（新增一版）'}
          </button>
          <button className="btn btn-sm btn-ghost" onClick={onClose}>收起</button>
        </div>
      </div>

      {/* 审核批次版本化：重审不再物理覆盖旧断言，用户可回看并指定生效版本 */}
      <VersionBar
        versions={options}
        selectedId={shownBatchId ?? ''}
        onSelect={(bid) => onPickBatch(content, bid)}
        latestId={batches[0]?.batch_id}
        activeId={activeId}
        onActivate={activeId ? (bid) => onActivateBatch(content.content_id, bid) : undefined}
        activating={activating}
        warnHint="只有生效版本决定这条内容的审核结论，也才会被报告引用。"
        emptyHint="该内容尚无审核批次"
      />

      <p className="review-content-title">
        {review.content.title || '无标题内容'} · {review.content.content_id.slice(0, 8)}
        <span className="note tnum"> · {review.claims.length} 条断言 · 人工复核 {manual} 条</span>
      </p>
      {review.claims.length === 0 && <p className="empty">该批次尚无断言，点「重新审核」抽取</p>}
      {review.claims.map(({ claim, evidence }) => (
        <ClaimRow key={claim.claim_id} claim={claim} evidenceCount={evidence.length} busy={busy === claim.claim_id} onDecide={onDecide} />
      ))}
      <NextSteps
        steps={[
          { label: '生成单篇报告', hint: '把这份断言与证据固化成可审计报告', onClick: () => onReport(content) },
          { label: '查看作品分析', hint: '这条内容为什么爆／为什么平淡的归因', onClick: () => onNext(content, 'analysis') },
          { label: '重新审核（新增一版）', hint: '重跑抽取与判定，旧版保留可回看', onClick: () => onReReview(content, true) },
        ]}
      />
    </div>
  )
})

function ClaimRow({ claim, evidenceCount, busy, onDecide }: {
  claim: Claim
  evidenceCount: number
  busy: boolean
  onDecide: (claimId: string, status: ClaimStatus) => void
}) {
  const claimTone = CLAIM_STATUS_COLOR[claim.status] ?? '#64748b'
  const confidence = claim.confidence == null ? null : Math.round(claim.confidence * 100)
  return (
    <div className="claim-row">
      <p className="claim-text">{claim.text}</p>
      <div className="claim-tags">
        <span className="chip" style={chipStyle(claimTone)}>{CLAIM_STATUS_LABEL[claim.status] ?? claim.status}</span>
        <span>{CLAIM_TYPE_LABEL[claim.claim_type ?? ''] ?? 'unknown'}</span>
        {confidence != null && (
          <span>
            置信度
            <span className="conf-meter"><span className="conf-fill" style={{ width: `${confidence}%` }} /></span>
            <b className="tnum"> {confidence}%</b>
          </span>
        )}
        <span>{evidenceCount} 条证据</span>
      </div>
      <div className="claim-actions">
        <button className="btn btn-sm" disabled={busy} onClick={() => onDecide(claim.claim_id, 'supported')}>标记支持</button>
        <button className="btn btn-sm" disabled={busy} onClick={() => onDecide(claim.claim_id, 'contradicted')}>标记反驳</button>
      </div>
    </div>
  )
}
