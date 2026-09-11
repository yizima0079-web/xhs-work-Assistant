import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { BrowserAction, BrowserConnectResult, BrowserStatus } from '../types'

const ACTION_LABEL: Record<BrowserAction, string> = {
  connected: '已连接小红书',
  opened: '已重新打开小红书',
  reused: '检测到已存在小红书页面',
  failed: '连接失败',
}

export default function BrowserPanel() {
  const [status, setStatus] = useState<BrowserStatus | null>(null)
  const [busy, setBusy] = useState<'connect' | 'retry' | null>(null)
  const [flash, setFlash] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  // 独立慢轮询（10s）：不打扰主列 2s 刷新
  useEffect(() => {
    let active = true
    const poll = async () => {
      try {
        const s = await api.browserStatus()
        if (active) { setStatus(s); setFlash(null) }
      } catch {
        if (active) setStatus(null) // 面板内降级显示，不污染顶层错误条
      }
    }
    poll()
    const t = setInterval(poll, 10000)
    return () => { active = false; clearInterval(t) }
  }, [])

  const act = async (kind: 'connect' | 'retry') => {
    setBusy(kind)
    setFlash(null)
    try {
      const r: BrowserConnectResult = kind === 'connect' ? await api.browserConnect() : await api.browserRetry()
      setStatus(await api.browserStatus().catch(() => null))
      setFlash({ kind: 'ok', text: r.detail ?? ACTION_LABEL[r.action] })
    } catch (e) {
      setFlash({ kind: 'err', text: e instanceof Error ? e.message : '连接失败' })
    } finally {
      setBusy(null)
    }
  }

  const linkState = !status
    ? { dot: 'dot warn', text: '状态不可达' }
    : status.reachable
      ? { dot: 'dot ok', text: 'Bridge 已连接' }
      : { dot: 'dot warn', text: 'Bridge 未就绪' }
  const xhsState = !status
    ? null
    : status.xhs_open
      ? { dot: 'dot ok', text: '已打开' }
      : { dot: 'dot', text: '未打开' }

  return (
    <section className="panel">
      <div className="panel-head">
        <h2>浏览器连接</h2>
        <span className="note">OpenCLI Bridge · Edge</span>
      </div>
      <div className="browser-box">
        <div className="browser-row">
          <span className={linkState.dot} />
          <span className="dim">连接状态</span>
          <b>{linkState.text}</b>
        </div>
        <div className="browser-row">
          <span className={xhsState?.dot ?? 'dot'} />
          <span className="dim">小红书页面</span>
          <b>{xhsState?.text ?? '未知'}</b>
        </div>
        {status?.reachable && (
          <p className="browser-meta">
            会话 <code>{status.session}</code> · <span className="tnum">{status.tabs.length}</span> 个标签页
            {status.tabs.length > 0 && status.tabs.length <= 6 && (
              <span className="browser-tabs">{status.tabs.map((t) => t.title ?? t.url).filter(Boolean).join(' / ')}</span>
            )}
          </p>
        )}
        {flash && (
          <div className={flash.kind === 'err' ? 'error-banner' : 'ok-banner'}>{flash.text}</div>
        )}
        <div className="btn-row">
          <button className="btn btn-primary" disabled={busy != null} onClick={() => act('connect')}>
            {busy === 'connect' ? '连接中…' : '开始连接'}
          </button>
          <button className="btn" disabled={busy != null} onClick={() => act('retry')}>
            {busy === 'retry' ? '检测中…' : '重试'}
          </button>
        </div>
      </div>
    </section>
  )
}
