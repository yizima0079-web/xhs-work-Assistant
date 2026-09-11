export function toNum(value: string | number | null | undefined): number {
  if (value == null) return 0
  if (typeof value === 'number') return Number.isFinite(value) ? value : 0
  const n = Number(String(value).replace(/,/g, ''))
  return Number.isFinite(n) ? n : 0
}

/** 中文简写：1234 → 1234，12000 → 1.2万，3.5亿。用于互动量等大数。 */
export function fmtCompact(value: number): string {
  if (!Number.isFinite(value)) return '—'
  if (value >= 1e8) return trimZero((value / 1e8).toFixed(1)) + '亿'
  if (value >= 1e4) return trimZero((value / 1e4).toFixed(1)) + '万'
  if (value >= 1000) return (value / 1000).toFixed(1).replace(/\.0$/, '') + 'k'
  return String(Math.round(value))
}

export function fmtCount(value: number): string {
  return Number.isFinite(value) ? String(Math.round(value)) : '—'
}

function trimZero(s: string): string {
  return s.replace(/\.0$/, '')
}

export function fmtPct(part: number, whole: number): string {
  if (whole <= 0) return '—'
  return `${Math.round((part / whole) * 1000) / 10}%`
}

export function sum(list: number[]): number {
  return list.reduce((a, b) => a + b, 0)
}
