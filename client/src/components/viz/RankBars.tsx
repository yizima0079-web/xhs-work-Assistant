import { fmtCompact } from '../../lib/format'

export interface RankRow {
  label: string
  value: number
  /** 副信息（如成功数），显示在右侧 value 之下。 */
  sub?: string
  color?: string
  active?: boolean
}

interface RankBarsProps {
  rows: RankRow[]
  max?: number
  /** 自定义 value 格式化，缺省用紧凑中文数字。 */
  formatValue?: (value: number) => string
  /** 点击某行。 */
  onSelect?: (row: RankRow, index: number) => void
}

/** 排名条形图：label + 相对比例条 + 数值。强调高对比与主次秩序。 */
export default function RankBars({ rows, max, formatValue = fmtCompact, onSelect }: RankBarsProps) {
  const peak = max ?? Math.max(1, ...rows.map((r) => r.value))
  return (
    <div className="rank-bars">
      {rows.map((row, index) => {
        const ratio = peak > 0 ? Math.max(0, Math.min(1, row.value / peak)) : 0
        const rank = index + 1
        const bar = (
          <div className="rank-row" key={`${row.label}-${index}`} onClick={onSelect ? () => onSelect(row, index) : undefined}>
            <span className="rank-idx tnum">{rank}</span>
            <div className="rank-main">
              <div className="rank-head">
                <span className="rank-label">{row.label}</span>
                <span className="rank-value tnum">
                  {formatValue(row.value)}
                  {row.sub && <em>{row.sub}</em>}
                </span>
              </div>
              <div className="rank-track">
                <div
                  className="rank-fill"
                  style={{
                    width: `${ratio * 100}%`,
                    background: row.active ? 'var(--accent)' : row.color ?? 'var(--bar-default)',
                  }}
                />
              </div>
            </div>
          </div>
        )
        return bar
      })}
      {rows.length === 0 && <p className="empty">暂无数据</p>}
    </div>
  )
}
