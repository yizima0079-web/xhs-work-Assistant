import { Fragment, useEffect, useMemo, useState } from 'react'
import type { FormEvent } from 'react'
import { api } from '../api/client'
import type { KbAnswer, KbDocument, KbQaPair, KbSearchHit } from '../types'

const SOURCE_META: Record<string, { label: string; color: string }> = {
  manual: { label: '手动文档', color: '#7c3aed' },
  analysis: { label: '作品分析', color: '#2563eb' },
  content: { label: '采集内容', color: '#0ea5e9' },
}

const STATUS_META: Record<string, { label: string; color: string }> = {
  pending: { label: '待向量化', color: '#d97706' },
  embedding: { label: '嵌入中', color: '#d97706' },
  ready: { label: '已入库', color: '#16a34a' },
  failed: { label: '失败', color: '#dc2626' },
}

const QA_STATUS_META: Record<string, { label: string; color: string }> = {
  draft: { label: '待审核', color: '#d97706' },
  approved: { label: '已通过', color: '#16a34a' },
  rejected: { label: '已驳回', color: '#dc2626' },
}

/** Q&A 拆解维度 → 中文标签（technique/persona/hook/structure/transfer/transfer_risk）。 */
const QA_DIM_LABEL: Record<string, string> = {
  technique: '表现手法',
  persona: 'IP 人设',
  hook: '开头钩子',
  structure: '结构节奏',
  transfer: '迁移建议',
  transfer_risk: '失败风险',
}

const FILE_ACCEPT = '.txt,.md,.markdown,.doc,.docx,.pdf'
const QA_HISTORY_KEY = 'datapp.kb.qa-history.v1'
type QaHistoryItem = { id: string; query: string; answer: string; createdAt: string; answered: boolean }

function sourceMeta(t: string) {
  return SOURCE_META[t] ?? { label: t, color: '#64748b' }
}
function statusMeta(s: string) {
  return STATUS_META[s] ?? { label: s, color: '#64748b' }
}
function qaStatusMeta(s: string) {
  return QA_STATUS_META[s] ?? { label: s, color: '#64748b' }
}

/** 把 pair.dimensions 拍平成可展示的「标签: 值」列表（列表值用逗号拼接）。 */
function qaDims(pair: KbQaPair): Array<{ label: string; value: string }> {
  const d = pair.dimensions ?? {}
  const rows: Array<{ label: string; value: string }> = []
  for (const [key, label] of Object.entries(QA_DIM_LABEL)) {
    const v = d[key]
    if (v == null || v === '') continue
    const text = Array.isArray(v) ? v.filter((x) => typeof x === 'string' && x).join('；') : String(v)
    if (text) rows.push({ label, value: text })
  }
  return rows
}

/** 从来源列表挑 doc 的 title/meta（search hit 自带 meta.title）。 */
function hitTitle(hit: KbSearchHit): string {
  const t = hit.meta?.title
  return typeof t === 'string' && t ? t : hit.doc_id.slice(0, 8)
}

/** 悬停预览内容：清洗后 markdown 优先，其次原始文本；只取前 100 行。 */
function previewLines(d: KbDocument): { text: string; kind: string } {
  const src = d.markdown?.trim() || d.raw_text?.trim() || ''
  const lines = src.split('\n')
  return {
    text: lines.slice(0, 100).join('\n'),
    kind: d.markdown?.trim() ? '清洗后 Markdown' : d.raw_text?.trim() ? '原始文本' : '（无正文，仅向量检索用）',
  }
}

export default function KnowledgeBase({ onBack }: { onBack: () => void }) {
  const [docs, setDocs] = useState<KbDocument[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())

  // 检索
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [hits, setHits] = useState<KbSearchHit[] | null>(null)

  // 知识问答（RAG）
  const [askQuery, setAskQuery] = useState('')
  const [askBusy, setAskBusy] = useState(false)
  const [askResult, setAskResult] = useState<KbAnswer | null>(null)
  const [askHistory, setAskHistory] = useState<QaHistoryItem[]>([])
  const [memoryEnabled, setMemoryEnabled] = useState(true)

  // Q&A 文档：展开审核 + 录入
  const [qaOpen, setQaOpen] = useState<string | null>(null)
  const [qaPairs, setQaPairs] = useState<Record<string, KbQaPair[]>>({})
  const [qaBusy, setQaBusy] = useState<string | null>(null)
  const [qaTitle, setQaTitle] = useState('')
  const [qaQuestion, setQaQuestion] = useState('')
  const [qaAnswer, setQaAnswer] = useState('')
  const [qaTags, setQaTags] = useState('')
  const [qaSaving, setQaSaving] = useState(false)

  // 手动上传 / 文件导入
  const [mTitle, setMTitle] = useState('')
  const [mText, setMText] = useState('')
  const [mTags, setMTags] = useState('')
  const [mUrl, setMUrl] = useState('')
  const [importName, setImportName] = useState<string | null>(null)
  const [parsing, setParsing] = useState(false)
  const [cleaning, setCleaning] = useState(false)

  // 列表操作锁
  const [acting, setActing] = useState(false)
  const [busyDoc, setBusyDoc] = useState<string | null>(null)

  const load = async () => {
    setLoading(true)
    try {
      const next = await api.listKbDocuments(100)
      setDocs(next)
      setError(null)
      const alive = new Set(next.map((d) => d.doc_id))
      setSelected((prev) => new Set([...prev].filter((id) => alive.has(id))))
    } catch (e) {
      setError(e instanceof Error ? e.message : '知识库加载失败')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(QA_HISTORY_KEY)
      if (!raw) return
      const parsed = JSON.parse(raw)
      if (Array.isArray(parsed)) setAskHistory(parsed.slice(-20))
    } catch {
      // 浏览器存储不可用时仍保留当前会话问答，不阻断知识库。
    }
  }, [])

  useEffect(() => {
    try { window.localStorage.setItem(QA_HISTORY_KEY, JSON.stringify(askHistory.slice(-20))) } catch { /* ignore storage quota */ }
  }, [askHistory])

  const counts = useMemo(() => {
    const c = { pending: 0, ready: 0, failed: 0, total: docs.length }
    for (const d of docs) {
      if (d.status in c) c[d.status as 'pending'] += 1
    }
    return c
  }, [docs])

  const allChecked = docs.length > 0 && selected.size === docs.length

  const search = async () => {
    const q = query.trim()
    if (!q) { setHits(null); return }
    setSearching(true)
    setError(null)
    try {
      setHits(await api.searchKb(q, 8))
    } catch (e) {
      setError(e instanceof Error ? e.message : '检索失败')
    } finally {
      setSearching(false)
    }
  }

  const ask = async () => {
    const q = askQuery.trim()
    if (!q) return
    setAskBusy(true)
    setError(null)
    try {
      const history = memoryEnabled
        ? askHistory.slice(-6).map(({ query, answer }) => ({ query, answer }))
        : []
      const result = await api.askKb(q, undefined, history)
      setAskResult(result)
      setAskHistory((prev) => [...prev, {
        id: crypto.randomUUID(), query: q,
        answer: result.answered ? result.answer : result.limitations.join('；') || '本次未找到可核验答案',
        createdAt: new Date().toISOString(), answered: result.answered,
      }].slice(-20))
      setAskQuery('')
    } catch (e) {
      setError(e instanceof Error ? e.message : '问答失败')
    } finally {
      setAskBusy(false)
    }
  }

  const clearAskHistory = () => {
    setAskHistory([])
    setAskResult(null)
  }

  const toggleQa = async (d: KbDocument) => {
    if (qaOpen === d.doc_id) { setQaOpen(null); return }
    setQaOpen(d.doc_id)
    if (qaPairs[d.doc_id]) return
    try {
      const pairs = await api.listQaPairs(d.doc_id)
      setQaPairs((prev) => ({ ...prev, [d.doc_id]: pairs }))
    } catch (e) {
      setError(e instanceof Error ? e.message : '问答对加载失败')
    }
  }

  const reviewQa = async (docId: string, qaId: string, status: 'approved' | 'rejected') => {
    setQaBusy(qaId)
    setError(null)
    try {
      const act = status === 'approved' ? api.approveQaPairs : api.rejectQaPairs
      await act([qaId])
      const pairs = await api.listQaPairs(docId)
      setQaPairs((prev) => ({ ...prev, [docId]: pairs }))
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '审核失败')
    } finally {
      setQaBusy(null)
    }
  }

  const deleteQa = async (docId: string, qaId: string) => {
    setQaBusy(qaId)
    setError(null)
    try {
      await api.deleteQaPair(qaId)
      const pairs = await api.listQaPairs(docId)
      setQaPairs((prev) => ({ ...prev, [docId]: pairs }))
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : '删除问答对失败')
    } finally {
      setQaBusy(null)
    }
  }

  const saveQa = async (e: FormEvent) => {
    e.preventDefault()
    const title = qaTitle.trim()
    const question = qaQuestion.trim()
    const answer = qaAnswer.trim()
    if (!title || !question || !answer) return
    setQaSaving(true)
    setError(null)
    setNotice(null)
    try {
      const tags = qaTags.split(/[,，;；\s]+/).filter(Boolean)
      const res = await api.createManualQa({ title, pairs: [{ question, answer, tags }], tags })
      setQaTitle(''); setQaQuestion(''); setQaAnswer(''); setQaTags('')
      setNotice(`已创建 Q&A 文档「${res.doc.title}」，${res.pairs.length} 条问答（草稿），审核通过后点「向量化」入库`)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Q&A 录入失败')
    } finally {
      setQaSaving(false)
    }
  }

  const onFile = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setParsing(true)
    setError(null)
    setNotice(null)
    setImportName(null)
    try {
      const parsed = await api.uploadKbFile(file)
      setMText(parsed.text || '')
      setImportName(parsed.filename)
      const base = parsed.filename.replace(/\.(txt|md|markdown|docx?|pdf)$/i, '')
      if (base && !mTitle.trim()) setMTitle(base)
      setNotice(`已解析 ${parsed.filename}（${parsed.text.length} 字符），文本已填入可编辑，请检查后「清洗并保存」`)
    } catch (err) {
      setError(err instanceof Error ? err.message : '文件解析失败')
    } finally {
      setParsing(false)
    }
  }

  const save = async (e: FormEvent) => {
    e.preventDefault()
    const title = mTitle.trim()
    if (!title || !mText.trim()) return
    setCleaning(true)
    setError(null)
    setNotice(null)
    try {
      const tags = mTags.split(/[,，;；\s]+/).filter(Boolean)
      const doc = await api.addKbDocument({ title, text: mText, tags, url: mUrl.trim() || null })
      setMTitle(''); setMText(''); setMTags(''); setMUrl(''); setImportName(null)
      setNotice(`已清洗暂存「${doc.title}」，状态=待向量化。点「向量化」完成入库后才会参与检索`)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : '清洗保存失败')
    } finally {
      setCleaning(false)
    }
  }

  const toggleOne = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id); else next.add(id)
      return next
    })
  }

  const toggleAll = () => {
    setSelected(allChecked ? new Set() : new Set(docs.map((d) => d.doc_id)))
  }

  const vectorizeOne = async (d: KbDocument) => {
    setBusyDoc(d.doc_id)
    setError(null)
    setNotice(null)
    try {
      await api.vectorizeKbDocument(d.doc_id)
      setNotice(`「${d.title}」已完成向量化入库`)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : '向量化失败')
      await load()
    } finally {
      setBusyDoc(null)
    }
  }

  const batchVectorize = async () => {
    const ids = [...selected]
    if (ids.length === 0) return
    setActing(true)
    setError(null)
    setNotice(null)
    try {
      const results = await api.vectorizeKbDocuments(ids)
      const ok = results.filter((r) => r.ok).length
      const bad = results.filter((r) => !r.ok)
      setNotice(`批量向量化：${ok} 成功${bad.length ? `，${bad.length} 失败（${bad.map((b) => b.error || '未知').join('；')}）` : ''}`)
      setSelected(new Set())
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : '批量向量化失败')
    } finally {
      setActing(false)
    }
  }

  const deleteOne = async (d: KbDocument) => {
    if (!window.confirm(`确认删除「${d.title}」及其全部 chunk？`)) return
    setBusyDoc(d.doc_id)
    setError(null)
    setNotice(null)
    try {
      await api.deleteKbDocument(d.doc_id)
      setNotice(`已删除「${d.title}」`)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败')
    } finally {
      setBusyDoc(null)
    }
  }

  const batchDelete = async () => {
    const ids = [...selected]
    if (ids.length === 0) return
    if (!window.confirm(`确认批量删除选中的 ${ids.length} 份文档（含其 chunk）？此操作不可恢复`)) return
    setActing(true)
    setError(null)
    setNotice(null)
    try {
      const r = await api.deleteKbDocuments(ids)
      setNotice(`已删除 ${r.deleted}/${r.requested} 份文档`)
      setSelected(new Set())
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : '批量删除失败')
    } finally {
      setActing(false)
    }
  }

  const listBusy = cleaning || acting || parsing

  return (
    <div className="app">
      <header className="page-header">
        <div className="brand">
          <p className="eyebrow">DATAPP · RAG KNOWLEDGE BASE</p>
          <h1>向量知识库</h1>
          <p className="tagline">手动文档：清洗暂存(pending) → 向量化后 ready 才可检索 · 系统产物一步入库</p>
        </div>
        <div className="header-actions">
          <button className="btn" onClick={load} disabled={loading || listBusy}>{loading ? '同步中…' : '刷新'}</button>
          <button className="btn btn-primary" onClick={onBack}>← 返回看板</button>
        </div>
      </header>

      {error && <div className="error-banner">{error}</div>}
      {notice && <div className="ok-banner">{notice}</div>}

      <div className="layout">
        <main className="col-main">
          {/* 语义检索 */}
          <section className="panel">
            <div className="panel-head">
              <h2>语义检索</h2>
              <span className="note">只命中已入库(ready)文档 · tongyi-embedding-vision-flash · 768 维</span>
            </div>
            <form
              className="kb-search-row"
              onSubmit={(e) => { e.preventDefault(); search() }}
            >
              <input
                className="field"
                placeholder="输入查询，如：AI 眼镜实测的翻车点在哪"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <button className="btn btn-primary" type="submit" disabled={searching || !query.trim()}>
                {searching ? '检索中…' : '检索'}
              </button>
            </form>

            {hits && hits.length === 0 && <p className="empty">无命中（暂无 ready 文档或语义无重叠）</p>}
            {hits && hits.length > 0 && (
              <div className="kb-hit-list">
                <p className="kb-hits-note">Top {hits.length} 命中</p>
                {hits.map((hit) => (
                  <article className="kb-hit" key={hit.chunk_id}>
                    <div className="kb-hit-head">
                      <div className="kb-doc-main">
                        <p className="kb-doc-title">{hitTitle(hit)}</p>
                        <div className="kb-doc-sub">
                          <span className="tag-chip">{sourceMeta(String(hit.meta?.source_type ?? 'manual')).label}</span>
                          <span className="tnum">{hit.doc_id.slice(0, 8)}</span>
                          {typeof hit.meta?.url === 'string' && hit.meta.url
                            ? <a href={hit.meta.url} target="_blank" rel="noreferrer">原文 ↗</a>
                            : null}
                        </div>
                      </div>
                      <div className="kb-hit-score tnum">{Math.round(hit.score * 100)}%</div>
                    </div>
                    <p className="kb-hit-text">{hit.text}</p>
                    {hit.modality === 'image' && hit.image_url && (
                      <a href={hit.image_url} target="_blank" rel="noreferrer">
                        <img className="kb-img" src={hit.image_url} alt="封面" referrerPolicy="no-referrer" />
                      </a>
                    )}
                  </article>
                ))}
              </div>
            )}
          </section>

          {/* 知识问答（RAG，带引用 + 无据拒答） */}
          <section className="panel">
            <div className="panel-head">
              <h2>知识问答</h2>
              <span className="note">基于已入库知识作答 · 带引用 · 无据拒答</span>
            </div>
            <form
              className="kb-search-row"
              onSubmit={(e) => { e.preventDefault(); ask() }}
            >
              <input
                className="field"
                placeholder="问一个知识库能回答的问题，如：为什么夸张演绎能爆"
                value={askQuery}
                onChange={(e) => setAskQuery(e.target.value)}
              />
              <button className="btn btn-primary" type="submit" disabled={askBusy || !askQuery.trim()}>
                {askBusy ? '思考中…' : '提问'}
              </button>
            </form>

            <div className="qa-memory-bar">
              <label className="qa-memory-toggle">
                <input type="checkbox" checked={memoryEnabled} onChange={(e) => setMemoryEnabled(e.target.checked)} />
                <span>记住本地会话上下文</span>
              </label>
              <span className="note">{askHistory.length ? `已保存 ${askHistory.length} 轮` : '暂无历史对话'}</span>
              {askHistory.length > 0 && <button className="btn btn-sm btn-ghost" type="button" onClick={clearAskHistory}>清空历史</button>}
            </div>
            {askHistory.length > 0 && (
              <div className="qa-history" aria-label="历史对话">
                {askHistory.slice(-5).reverse().map((item) => (
                  <button className="qa-history-item" type="button" key={item.id} onClick={() => { setAskQuery(item.query); setAskResult(null) }}>
                    <span className={`qa-history-state${item.answered ? ' ok' : ''}`}>{item.answered ? '已回答' : '未命中'}</span>
                    <span className="qa-history-question">{item.query}</span>
                    <span className="qa-history-answer">{item.answer}</span>
                  </button>
                ))}
              </div>
            )}

            {askResult && (
              <div className="qa-answer">
                {askResult.answered ? (
                  <>
                    <p className="qa-answer-text">{askResult.answer}</p>
                    {askResult.citations.length > 0 && (
                      <div className="qa-cites">
                        <p className="qa-cites-head">引用来源（{askResult.citations.length}）</p>
                        {askResult.citations.map((c) => (
                          <div className="qa-cite" key={c.chunk_id}>
                            <div className="qa-cite-head">
                              <span className={`qa-verified${c.verified ? ' ok' : ''}`}>
                                {c.verified ? '✓ 逐字可查' : '△ 引用改写'}
                              </span>
                              <span className="qa-cite-title">
                                {c.question || c.title || c.doc_id.slice(0, 8)}
                              </span>
                              <span className="tnum">{Math.round(c.score * 100)}%</span>
                            </div>
                            <p className="qa-cite-text">{c.text}</p>
                          </div>
                        ))}
                      </div>
                    )}
                  </>
                ) : (
                  <div className="qa-refuse">
                    <p className="qa-refuse-head">⚠ 无据拒答</p>
                    <p className="qa-refuse-reason">{askResult.limitations.join('；') || '知识库中没有足够依据回答此问题'}</p>
                    <p className="qa-refuse-meta tnum">
                      {askResult.reason && <span>{askResult.reason}</span>}
                      {askResult.retrieval_count > 0 && <span>检索 {askResult.retrieval_count} 条</span>}
                      {askResult.top_score > 0 && <span>最高相似 {askResult.top_score.toFixed(2)}</span>}
                    </p>
                  </div>
                )}
              </div>
            )}
          </section>

          {/* 文档列表 */}
          <section className="panel">
            <div className="panel-head">
              <h2>知识库文档</h2>
              <span className="note tnum">
                {docs.length} 份 · 待向量化 {counts.pending} · 已入库 {counts.ready}
                {counts.failed > 0 ? ` · 失败 ${counts.failed}` : ''}
              </span>
            </div>

            {docs.length > 0 && (
              <div className="kb-toolbar">
                <label className="kb-selall">
                  <input type="checkbox" checked={allChecked} onChange={toggleAll} disabled={listBusy} />
                  全选
                </label>
                <span className="note tnum">已选 {selected.size}</span>
                <div className="spacer" />
                <button
                  className="btn"
                  onClick={batchVectorize}
                  disabled={selected.size === 0 || acting || cleaning}
                >
                  {acting ? '处理中…' : '批量向量化'}
                </button>
                <button
                  className="btn btn-danger"
                  onClick={batchDelete}
                  disabled={selected.size === 0 || acting || cleaning}
                >
                  批量删除
                </button>
              </div>
            )}

            {docs.length === 0 ? (
              <p className="empty">暂无文档：去看板分析作品后点「保存到知识库」，或在右侧上传文档</p>
            ) : (
              <div className="kb-doc-list">
                {docs.map((d) => {
                  const src = sourceMeta(d.source_type)
                  const st = statusMeta(d.status)
                  const canVec = d.status === 'pending' || d.status === 'failed'
                  const busy = busyDoc === d.doc_id
                  const pv = previewLines(d)
                  const isQa = d.doc_type === 'qa'
                  const open = qaOpen === d.doc_id
                  const pairs = qaPairs[d.doc_id] ?? []
                  return (
                    <Fragment key={d.doc_id}>
                      <article className="kb-doc kb-row">
                        <label className="kb-check" onClick={(e) => e.stopPropagation()}>
                          <input
                            type="checkbox"
                            checked={selected.has(d.doc_id)}
                            onChange={() => toggleOne(d.doc_id)}
                            disabled={acting || cleaning}
                          />
                        </label>

                        <div className="kb-preview-wrap">
                          <div className="kb-doc-main">
                            <p className="kb-doc-title">{d.title || d.doc_id.slice(0, 8)}</p>
                            <div className="kb-doc-sub">
                              <span className="tag-chip" style={{ color: src.color, borderColor: `${src.color}55`, background: `${src.color}14` }}>{src.label}</span>
                              {isQa && (
                                <span className="tag-chip" style={{ color: '#7c3aed', borderColor: '#7c3aed55', background: '#7c3aed14' }}>
                                  Q&A {d.qa_pair_count} 条{d.qa_approved_count > 0 ? ` · 通过 ${d.qa_approved_count}` : ''}
                                </span>
                              )}
                              {d.author && <span>@{d.author}</span>}
                              {d.tags.slice(0, 4).map((t) => <span key={t}>#{t}</span>)}
                              {d.url && <a href={d.url} target="_blank" rel="noreferrer">来源 ↗</a>}
                            </div>
                          </div>
                          {pv.text && (
                            <div className="kb-preview-pop">
                              <p className="kb-preview-head">{pv.kind} · 前 {Math.min(pv.text.split('\n').length, 100)} 行</p>
                              <pre className="kb-preview-body">{pv.text}</pre>
                            </div>
                          )}
                        </div>

                        <div className="kb-doc-right">
                          <span className="chip" style={{ color: st.color, background: `${st.color}14`, borderColor: `${st.color}44` }}>
                            <span className="dot" style={{ background: st.color }} />
                            {st.label}
                          </span>
                          <div className="kb-row-ops">
                            {isQa && (
                              <button className="btn btn-sm" onClick={() => toggleQa(d)}>
                                {open ? '收起问答对' : '审核问答对'}
                              </button>
                            )}
                            <button
                              className="btn btn-sm"
                              disabled={!canVec || busy || acting || cleaning}
                              onClick={() => vectorizeOne(d)}
                              title={canVec ? '分块嵌入后入库（ready 后参与检索）' : '已入库，无需重复向量化'}
                            >
                              {busy ? '处理中…' : '向量化'}
                            </button>
                            <button
                              className="btn btn-sm btn-danger"
                              disabled={busy || acting || cleaning}
                              onClick={() => deleteOne(d)}
                            >
                              删除
                            </button>
                          </div>
                          <div className="kb-doc-sub tnum" style={{ justifyContent: 'flex-end' }}>
                            {d.chunk_count} chunk · {d.created_at.slice(0, 10)}
                          </div>
                        </div>
                      </article>

                      {isQa && open && (
                        <div className="qa-pairs">
                          {pairs.length === 0 ? (
                            <p className="empty">该文档暂无问答对</p>
                          ) : (
                            pairs.map((p) => {
                              const ps = qaStatusMeta(p.status)
                              const dims = qaDims(p)
                              return (
                                <div className="qa-pair" key={p.qa_id}>
                                  <div className="qa-pair-head">
                                    <span className="chip" style={{ color: ps.color, background: `${ps.color}14`, borderColor: `${ps.color}44` }}>
                                      <span className="dot" style={{ background: ps.color }} />
                                      {ps.label}
                                    </span>
                                    <p className="qa-pair-q">Q：{p.question}</p>
                                  </div>
                                  <p className="qa-pair-a">A：{p.answer}</p>
                                  {dims.length > 0 && (
                                    <div className="qa-pair-dims">
                                      {dims.map((dm) => (
                                        <span key={dm.label}><b>{dm.label}</b> {dm.value}</span>
                                      ))}
                                    </div>
                                  )}
                                  {p.evidence.length > 0 && (
                                    <p className="qa-pair-evi tnum">
                                      证据 {p.evidence.length} 条 · {p.evidence.map((e) => e.ref.slice(0, 8)).join(', ')}
                                    </p>
                                  )}
                                  <div className="qa-pair-ops">
                                    {p.status !== 'approved' && (
                                      <button className="btn btn-sm" disabled={qaBusy === p.qa_id} onClick={() => reviewQa(d.doc_id, p.qa_id, 'approved')}>
                                        通过
                                      </button>
                                    )}
                                    {p.status !== 'rejected' && (
                                      <button className="btn btn-sm btn-danger" disabled={qaBusy === p.qa_id} onClick={() => reviewQa(d.doc_id, p.qa_id, 'rejected')}>
                                        驳回
                                      </button>
                                    )}
                                    <button className="btn btn-sm btn-danger-ghost" disabled={qaBusy === p.qa_id} onClick={() => deleteQa(d.doc_id, p.qa_id)}>
                                      删除
                                    </button>
                                  </div>
                                </div>
                              )
                            })
                          )}
                        </div>
                      )}
                    </Fragment>
                  )
                })}
              </div>
            )}
          </section>
        </main>

        {/* 上传：文件导入 + 手动文段 → 清洗暂存 */}
        <aside className="col-side">
          <section className="panel">
            <div className="panel-head">
              <h2>文档入库</h2>
              <span className="note">txt / md / docx / pdf / doc</span>
            </div>
            <form className="form-grid" onSubmit={save}>
              <div>
                <label className="field-label" htmlFor="kb-file">导入文件（.txt/.md/.docx/.pdf）</label>
                <input id="kb-file" className="field" type="file" accept={FILE_ACCEPT} onChange={onFile} disabled={parsing} />
                {importName && <p className="hint ok">已导入 {importName}，正文已回填，可继续编辑</p>}
              </div>
              <div>
                <label className="field-label" htmlFor="kb-title">标题</label>
                <input id="kb-title" className="field" value={mTitle} onChange={(e) => setMTitle(e.target.value)} placeholder="如：AI 眼镜行业报告要点" />
              </div>
              <div>
                <label className="field-label" htmlFor="kb-text">正文 / 文段（保存时自动清洗 → 标题分级 markdown）</label>
                <textarea id="kb-text" className="field kb-textarea" rows={12} value={mText} onChange={(e) => setMText(e.target.value)} placeholder="粘贴文本内容… 未清洗文本不会入库" />
              </div>
              <div>
                <label className="field-label" htmlFor="kb-tags">标签（逗号分隔，可选）</label>
                <input id="kb-tags" className="field" value={mTags} onChange={(e) => setMTags(e.target.value)} placeholder="AI, 眼镜, 测评" />
              </div>
              <div>
                <label className="field-label" htmlFor="kb-url">来源 URL（可选）</label>
                <input id="kb-url" className="field" value={mUrl} onChange={(e) => setMUrl(e.target.value)} placeholder="https://…" />
              </div>
              <button className="btn btn-primary" type="submit" disabled={cleaning || parsing || !mTitle.trim() || !mText.trim()}>
                {cleaning ? '清洗中…（模型处理约需数十秒）' : '清洗并保存（暂存待向量化）'}
              </button>
            </form>
            <p className="hint" style={{ marginTop: 10 }}>
              保存只做清洗并暂存（pending）。逐个/批量「向量化」后才 ready 入库可检索；删除随时可执行。
            </p>
          </section>

          <section className="panel">
            <div className="panel-head">
              <h2>Q&A 录入</h2>
              <span className="note">人写即终态 · 无需模型</span>
            </div>
            <form className="form-grid" onSubmit={saveQa}>
              <div>
                <label className="field-label" htmlFor="qa-title">文档标题</label>
                <input id="qa-title" className="field" value={qaTitle} onChange={(e) => setQaTitle(e.target.value)} placeholder="如：爆款演绎手法拆解" />
              </div>
              <div>
                <label className="field-label" htmlFor="qa-q">问题</label>
                <input id="qa-q" className="field" value={qaQuestion} onChange={(e) => setQaQuestion(e.target.value)} placeholder="如：为什么夸张演绎能爆？" />
              </div>
              <div>
                <label className="field-label" htmlFor="qa-a">答案</label>
                <textarea id="qa-a" className="field kb-textarea" rows={6} value={qaAnswer} onChange={(e) => setQaAnswer(e.target.value)} placeholder="结论 + 拆解 + 归因 + 迁移建议 + 失败风险" />
              </div>
              <div>
                <label className="field-label" htmlFor="qa-tags">标签（逗号分隔，可选）</label>
                <input id="qa-tags" className="field" value={qaTags} onChange={(e) => setQaTags(e.target.value)} placeholder="爆款拆解, 演绎手法" />
              </div>
              <button className="btn btn-primary" type="submit" disabled={qaSaving || !qaTitle.trim() || !qaQuestion.trim() || !qaAnswer.trim()}>
                {qaSaving ? '保存中…' : '录入为问答草稿'}
              </button>
            </form>
            <p className="hint" style={{ marginTop: 10 }}>
              录入后为草稿（待审核）；在左侧文档列表点「审核问答对」通过后，再「向量化」入库参与检索与问答。
            </p>
          </section>
        </aside>
      </div>
    </div>
  )
}
