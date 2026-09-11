import { useEffect, useRef } from 'react'

export interface ConfirmDialogProps {
  open: boolean
  title: string
  /** 说明正文；影响面明细请用 items 传，便于逐行对齐 */
  message?: string
  items?: string[]
  note?: string
  confirmLabel?: string
  cancelLabel?: string
  /** 不可逆操作置 true：确认键走红色，默认焦点落在取消键上防误触 */
  danger?: boolean
  busy?: boolean
  onConfirm: () => void
  onCancel: () => void
}

/**
 * 不可逆操作的二次确认弹窗（模态遮罩 + Esc 关闭 + 焦点落位）。
 *
 * 刻意不用 window.confirm：删除要列出逐项影响面、入库要点名「第几版」，
 * 原生弹窗表达不了，也无法用既有设计系统着色。
 */
export function ConfirmDialog({
  open,
  title,
  message,
  items,
  note,
  confirmLabel = '确认',
  cancelLabel = '取消',
  danger = false,
  busy = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) onCancel()
    }
    window.addEventListener('keydown', onKey)
    // 危险操作默认焦点给「取消」，避免一个回车就把不可逆的事做了
    cancelRef.current?.focus()
    return () => window.removeEventListener('keydown', onKey)
  }, [open, busy, onCancel])

  if (!open) return null

  return (
    <div
      className="dialog-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget && !busy) onCancel()
      }}
    >
      <div className="dialog" role="dialog" aria-modal="true" aria-label={title}>
        <h3 className="dialog-title">{title}</h3>
        {message && <p className="dialog-message">{message}</p>}
        {items && items.length > 0 && (
          <ul className="dialog-items">
            {items.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        )}
        {note && <p className="dialog-note">{note}</p>}
        <div className="dialog-actions">
          <button className="btn" onClick={onCancel} disabled={busy} ref={cancelRef}>
            {cancelLabel}
          </button>
          <button
            className={`btn ${danger ? 'btn-danger' : 'btn-primary'}`}
            onClick={onConfirm}
            disabled={busy}
          >
            {busy ? '处理中…' : confirmLabel}
          </button>
        </div>
      </div>
    </div>
  )
}
