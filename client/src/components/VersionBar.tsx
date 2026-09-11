export interface VersionOption {
  id: string
  /** 「第 3 版」 */
  label: string
  /** 副信息（时间 / ✓已入库） */
  sublabel?: string
  /** 状态徽标（最新 / 生效 / 已入库） */
  badge?: string
}

export interface VersionBarProps {
  versions: VersionOption[]
  selectedId: string
  onSelect: (id: string) => void
  /** 最新一版的 id：选中非最新版时亮警示条 */
  latestId?: string
  /** 生效版本的 id（审核批次：生效那版决定内容状态） */
  activeId?: string
  onActivate?: (id: string) => void
  activating?: boolean
  /** 警示条里补充的操作提示，如「入库前请确认这就是你要的那一版」 */
  warnHint?: string
  emptyHint?: string
}

/**
 * 结果版本条：挑版本 + 提醒你正在操作哪一版。
 *
 * 多版本并存最大风险是「以为在改/入库最新版，其实停在旧版」，所以：
 * 选中的不是最新版时**常驻**醒目警示条，并给一键「切到最新」。
 */
export function VersionBar({
  versions,
  selectedId,
  onSelect,
  latestId,
  activeId,
  onActivate,
  activating = false,
  warnHint,
  emptyHint = '暂无历史版本',
}: VersionBarProps) {
  if (versions.length === 0) {
    return <p className="empty left">{emptyHint}</p>
  }

  const selected = versions.find((v) => v.id === selectedId) ?? versions[0]
  const latest = latestId ? versions.find((v) => v.id === latestId) : versions[0]
  const isStale = Boolean(latest && selected.id !== latest.id)
  const notActive = Boolean(activeId && selected.id !== activeId)
  const active = activeId ? versions.find((v) => v.id === activeId) : undefined

  return (
    <div className="version-bar-wrap">
      <div className="version-bar">
        <span className="version-label">版本</span>
        <select
          className="field field-inline"
          value={selected.id}
          onChange={(e) => onSelect(e.target.value)}
          title="选择要查看/操作的版本"
        >
          {versions.map((v) => (
            <option key={v.id} value={v.id}>
              {v.label}
              {v.badge ? ` · ${v.badge}` : ''}
              {v.sublabel ? ` · ${v.sublabel}` : ''}
            </option>
          ))}
        </select>
        {versions.length > 1 && (
          <span className="version-count tnum">{versions.length} 版</span>
        )}
        <span className="spacer" />
        {isStale && latest && (
          <button className="btn btn-sm" onClick={() => onSelect(latest.id)}>
            切到最新
          </button>
        )}
        {onActivate && notActive && (
          <button
            className="btn btn-sm btn-light-green"
            onClick={() => onActivate(selected.id)}
            disabled={activating}
          >
            {activating ? '切换中…' : '设为生效版本'}
          </button>
        )}
      </div>

      {isStale && latest && (
        <p className="version-warn" role="status">
          <b>⚠ 正在查看 {selected.label}</b>，最新为 {latest.label}。
          {/* 既不是最新、又不是生效版本时两件事都要说清，否则用户切回最新版仍可能不是生效版本 */}
          {active && notActive ? `当前生效的是 ${active.label}。` : ''}
          {warnHint ? ` ${warnHint}` : ' 下面的操作都作用于这一版，请确认后再继续。'}
          <button className="version-warn-act" onClick={() => onSelect(latest.id)}>
            切到 {latest.label}
          </button>
        </p>
      )}

      {!isStale && notActive && active && (
        <p className="version-warn" role="status">
          <b>⚠ {selected.label} 不是生效版本</b>，当前生效的是 {active.label}。
          入库/报告请以生效版本为准，或点右侧「设为生效版本」。
        </p>
      )}
    </div>
  )
}
