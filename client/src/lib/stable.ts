/**
 * 轮询去重：内容没变就保持原引用，让 setState 不触发重渲染。
 *
 * 看板每条数据都来自轮询。若每次都 setState 一个新数组，React 一律重渲染、
 * useMemo 一律重算，卡片上的入场动画也会被反复打断。这里按 JSON 内容比较
 * （这些接口的返回都是同一后端序列化出来的，键序稳定，可以直接比对）。
 */

export function sameJson(a: unknown, b: unknown): boolean {
  if (a === b) return true
  try {
    return JSON.stringify(a) === JSON.stringify(b)
  } catch {
    return false // 环形/不可序列化：保守地认为「变了」，宁可多渲染一次
  }
}

/** 内容未变则返回 prev（引用不变），变了才返回 next。 */
export function keepIfSame<T>(prev: T, next: T): T {
  return sameJson(prev, next) ? prev : next
}

/**
 * 列表级去重：逐条按 id 比对，未变的那条复用**旧对象引用**。
 *
 * 只做整批 keepIfSame 不够：后端每次返回的都是全新对象，只要有一条变了，
 * 整批都是新引用，memo 化的卡片会全体重渲染。这里把「哪一条真的变了」挑出来，
 * 其余保持原引用，React.memo 才真正挡得住。
 */
export function keepItemsById<T>(prev: T[], next: T[], keyOf: (item: T) => string): T[] {
  if (prev.length === 0) return next
  const byId = new Map(prev.map((item) => [keyOf(item), item]))
  let changed = prev.length !== next.length
  const merged = next.map((item, i) => {
    const old = byId.get(keyOf(item))
    if (old !== undefined && sameJson(old, item)) {
      if (prev[i] !== old) changed = true // 顺序变了也算变（卡片 key 复用，但展示次序不同）
      return old
    }
    changed = true
    return item
  })
  return changed ? merged : prev
}
