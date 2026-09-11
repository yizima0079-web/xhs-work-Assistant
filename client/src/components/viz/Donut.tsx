export interface DonutSegment {
  label: string
  value: number
  color: string
}

interface DonutProps {
  data: DonutSegment[]
  size?: number
  thickness?: number
  centerValue: string
  centerCaption: string
  /** 点击某段（可选）。 */
  onSelect?: (index: number) => void
}

/** 环形构成图（SVG stroke-dasharray，无依赖）。空数据渲染灰环。 */
export default function Donut({
  data,
  size = 172,
  thickness = 18,
  centerValue,
  centerCaption,
  onSelect,
}: DonutProps) {
  const total = data.reduce((acc, d) => acc + d.value, 0)
  const radius = (size - thickness) / 2
  const circumference = 2 * Math.PI * radius
  const view = size

  let cursor = 0
  const arcs = data.map((seg, index) => {
    const fraction = total > 0 ? seg.value / total : 0
    const length = fraction * circumference
    const arc = (
      <circle
        key={`${seg.label}-${index}`}
        cx={view / 2}
        cy={view / 2}
        r={radius}
        fill="none"
        stroke={seg.color}
        strokeWidth={thickness}
        strokeDasharray={total > 0 ? `${Math.max(length - 1.5, 0.5)} ${circumference - length}` : '0 0'}
        strokeDashoffset={-cursor}
        transform={`rotate(-90 ${view / 2} ${view / 2})`}
        opacity={seg.value === 0 ? 0 : 1}
        style={onSelect ? { cursor: 'pointer' } : undefined}
        onClick={onSelect && seg.value > 0 ? () => onSelect(index) : undefined}
      >
        {seg.value > 0 && <title>{`${seg.label} ${seg.value}`}</title>}
      </circle>
    )
    cursor += length
    return arc
  })

  return (
    <div className="donut" style={{ width: size, height: size }} role="img" aria-label={centerCaption}>
      <svg width={size} height={size} viewBox={`0 0 ${view} ${view}`}>
        <circle
          cx={view / 2}
          cy={view / 2}
          r={radius}
          fill="none"
          stroke="var(--track)"
          strokeWidth={thickness}
        />
        {total > 0 && arcs}
      </svg>
      <div className="donut-center">
        <strong className="tnum">{centerValue}</strong>
        <span>{centerCaption}</span>
      </div>
    </div>
  )
}
