import { toNum } from '../../lib/format'

export interface MiniColumn {
  /** 悬停说明，如任务目标。 */
  label?: string
  found: number
  saved: number
}

interface MiniColumnsProps {
  data: MiniColumn[]
  height?: number
  /** 画布内柱子保留的横向 padding（px）。 */
  gap?: number
}

/** 迷你双序列柱状图：发现(暗) vs 保存(强调)，直观呈现采集命中落差。 */
export default function MiniColumns({ data, height = 64, gap = 3 }: MiniColumnsProps) {
  const peak = Math.max(1, ...data.map((d) => Math.max(d.found, d.saved)))
  const width = Math.max(120, data.length * 14 + (data.length - 1) * gap)
  const base = height - 4
  return (
    <svg
      className="mini-cols"
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="最近任务 发现/保存 迷你柱状图"
      preserveAspectRatio="none"
    >
      <line x1={0} y1={base + 0.5} x2={width} y2={base + 0.5} stroke="var(--line)" strokeWidth={1} />
      {data.map((d, index) => {
        const foundH = (d.found / peak) * base
        const savedH = (d.saved / peak) * base
        const x = index * (14 + gap)
        const savedColor = d.saved >= d.found && d.found > 0 ? 'var(--accent)' : 'var(--accent-dim)'
        return (
          <g key={index}>
            {d.found > 0 && (
              <rect x={x} y={base - foundH} width={6} height={foundH} fill="var(--bar-mute)" rx={1.5}>
                {d.label && <title>{`${d.label} 发现 ${toNum(d.found)}`}</title>}
              </rect>
            )}
            {d.saved > 0 && (
              <rect x={x + 8} y={base - savedH} width={6} height={savedH} fill={savedColor} rx={1.5}>
                {d.label && <title>{`${d.label} 保存 ${toNum(d.saved)}`}</title>}
              </rect>
            )}
          </g>
        )
      })}
    </svg>
  )
}
