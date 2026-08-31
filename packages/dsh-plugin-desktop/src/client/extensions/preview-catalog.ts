import snapshot from '../../../../../plugin-catalog/plugins.json'

export interface PreviewCatalogEntry {
  id: string
  displayName: string
  repo: string
  category: string
  tarball?: string
  descriptionZh: string
  descriptionEn: string
}

export interface PreviewCatalogPayload {
  query?: unknown
  category?: unknown
  offset?: unknown
  limit?: unknown
}

export interface PreviewCatalogPage {
  total: number
  offset: number
  categories: Array<{ id: string; count: number }>
  entries: PreviewCatalogEntry[]
}

const entries = snapshot.entries satisfies PreviewCatalogEntry[]

/**
 * 浏览器直开官方 Runtime 时复用打包目录快照。
 *
 * 这里只做内存分页和搜索，不提供安装或配置写入能力。
 */
export function listPreviewCatalog(payload: PreviewCatalogPayload): PreviewCatalogPage {
  const query = typeof payload.query === 'string' ? payload.query.trim().toLocaleLowerCase() : ''
  const category = typeof payload.category === 'string' ? payload.category : ''
  const offset = boundedInteger(payload.offset, 0, Number.MAX_SAFE_INTEGER, 0)
  const limit = boundedInteger(payload.limit, 1, 50, 30)
  const filtered = entries.filter((entry) => {
    if (category !== '' && entry.category !== category) return false
    if (query === '') return true
    return `${entry.id} ${entry.displayName} ${entry.category} ${entry.descriptionZh} ${entry.descriptionEn}`
      .toLocaleLowerCase()
      .includes(query)
  })
  const counts = new Map<string, number>()
  for (const entry of entries) counts.set(entry.category, (counts.get(entry.category) ?? 0) + 1)
  return {
    total: filtered.length,
    offset,
    categories: [...counts].map(([id, count]) => ({ id, count })),
    entries: filtered.slice(offset, offset + limit),
  }
}

function boundedInteger(value: unknown, minimum: number, maximum: number, fallback: number): number {
  if (typeof value !== 'number' || !Number.isInteger(value)) return fallback
  return Math.min(maximum, Math.max(minimum, value))
}
