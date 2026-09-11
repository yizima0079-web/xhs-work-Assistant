export type LogLevel = 'info' | 'warn' | 'error'

export interface ClientLog {
  ts: string
  level: LogLevel
  event: string
  requestId?: string
  detail?: string
}

const MAX_DETAIL_LENGTH = 240

function clean(value: unknown): string | undefined {
  if (value == null) return undefined
  return String(value).replace(/authorization|bearer|cookie|token|xsec_token/gi, '[redacted]').slice(0, MAX_DETAIL_LENGTH)
}

export function writeClientLog(level: LogLevel, event: string, detail?: unknown, requestId?: string): void {
  const entry: ClientLog = { ts: new Date().toISOString(), level, event, requestId, detail: clean(detail) }
  const line = JSON.stringify(entry)
  if (level === 'error') console.error(line)
  else if (level === 'warn') console.warn(line)
  else console.info(line)
}
