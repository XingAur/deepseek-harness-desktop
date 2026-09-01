import type { DesktopBridgeLike } from '../desktop-bridge'

/** 单日 × 单模型的用量条目(`day` 空串表示汇总行 `totals`,面板不直接渲染)。 */
export interface UsageEntry {
  day: string
  model: string
  requests: number
  inputTokens: number
  outputTokens: number
  cacheCreationTokens: number
  cacheReadTokens: number
}

export interface UsageSummary {
  entries: UsageEntry[]
  totals: UsageEntry
  sessionsScanned: number
  filesScanned: number
  failures: string[]
}

export async function fetchUsageSummary(bridge: DesktopBridgeLike): Promise<UsageSummary> {
  return bridge.requestV2<UsageSummary>('usage.summary')
}

/** 按日视图:跨模型合并后的单日用量,按日期升序(条形图数据)。 */
export interface UsageDay {
  day: string
  requests: number
  inputTokens: number
  outputTokens: number
}

export function groupByDay(entries: UsageEntry[]): UsageDay[] {
  const days = new Map<string, UsageDay>()
  for (const entry of entries) {
    if (entry.day === '') continue
    const existing = days.get(entry.day)
    if (existing !== undefined) {
      existing.requests += entry.requests
      existing.inputTokens += entry.inputTokens
      existing.outputTokens += entry.outputTokens
    } else {
      days.set(entry.day, {
        day: entry.day,
        requests: entry.requests,
        inputTokens: entry.inputTokens,
        outputTokens: entry.outputTokens,
      })
    }
  }
  return [...days.values()].sort((left, right) => left.day.localeCompare(right.day))
}

/** 按模型视图:跨日期合并后的模型用量,按输出 tokens 降序(表格数据)。 */
export interface UsageModelRow {
  model: string
  requests: number
  inputTokens: number
  outputTokens: number
}

export function groupByModel(entries: UsageEntry[]): UsageModelRow[] {
  const models = new Map<string, UsageModelRow>()
  for (const entry of entries) {
    const existing = models.get(entry.model)
    if (existing !== undefined) {
      existing.requests += entry.requests
      existing.inputTokens += entry.inputTokens
      existing.outputTokens += entry.outputTokens
    } else {
      models.set(entry.model, {
        model: entry.model,
        requests: entry.requests,
        inputTokens: entry.inputTokens,
        outputTokens: entry.outputTokens,
      })
    }
  }
  return [...models.values()].sort((left, right) =>
    right.outputTokens - left.outputTokens || right.requests - left.requests || left.model.localeCompare(right.model))
}

/** 条形图宽度:按最大值归一化为 2-100 的百分比;最大值为 0 时恒 0。 */
export function barWidthPercent(value: number, max: number): number {
  if (max <= 0) return 0
  return Math.max(2, Math.round((value / max) * 100))
}

/** tokens 展示缩写:≥1 万折为 k、≥100 万折为 M,其余原样。 */
export function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${trimNumber(value / 1_000_000)}M`
  if (value >= 10_000) return `${trimNumber(value / 1_000)}k`
  return String(value)
}

function trimNumber(value: number): string {
  return value.toFixed(1).replace(/\.0$/, '')
}
