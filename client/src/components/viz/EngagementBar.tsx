import type { Engagement } from '../../types'
import { fmtCompact, toNum } from '../../lib/format'

/**
 * 互动四连条（点赞/评论/转发/收藏）。
 * 四条横向 bar 按各自数值比例取宽，入场 @keyframes growX 从 0 撑开。
 * 语义色：点赞 accent / 评论 violet / 转发 ok / 收藏 amber。
 */
const ROWS: Array<{ key: keyof Engagement; label: string; color: string }> = [
  { key: 'likes', label: '点赞', color: 'var(--accent)' },
  { key: 'comments', label: '评论', color: 'var(--violet)' },
  { key: 'shares', label: '转发', color: 'var(--ok)' },
  { key: 'collects', label: '收藏', color: 'var(--amber)' },
]

export default function EngagementBar({ engagement, className }: { engagement?: Engagement | null; className?: string }) {
  const e = (engagement ?? {}) as Partial<Engagement>
  const values = ROWS.map((r) => toNum(e[r.key]))
  const max = Math.max(...values, 1)

  return (
    <div className={`eng-bar${className ? ` ${className}` : ''}`}>
      {ROWS.map((r, i) => {
        const v = values[i]
        const pct = max > 0 ? Math.max((v / max) * 100, v > 0 ? 4 : 0) : 0
        return (
          <div className="eng-row" key={r.key}>
            <span className="eng-label">{r.label}</span>
            <span className="eng-track">
              <span
                className="eng-fill"
                style={{ width: `${pct}%`, background: r.color, animationDelay: `${i * 90}ms` }}
                title={`${r.label} ${fmtCompact(v)}`}
              />
            </span>
            <b className="eng-num tnum">{v > 0 ? fmtCompact(v) : '—'}</b>
          </div>
        )
      })}
    </div>
  )
}
