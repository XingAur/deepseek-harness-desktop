import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { messageOf } from '../model-agent/state'

/**
 * 插件市场：awesome-dsh-plugin 目录快照 + 官方 CLI 安装。
 *
 * 数据经 Rust 侧分页搜索（桥单帧 32KiB 限制），安装是可轮询的后台任务；
 * 安装的是第三方代码，界面必须持续可见风险提示。
 */

export interface PluginMarketProps {
  bridge: DesktopBridgeLike
}

interface CatalogEntry {
  id: string
  displayName: string
  repo: string
  category: string
  tarball?: string
  descriptionZh: string
  descriptionEn: string
}

interface CatalogPage {
  total: number
  offset: number
  categories: Array<{ id: string; count: number }>
  entries: CatalogEntry[]
}

interface InstallStatus {
  jobRunning: boolean
  jobOutput: string[]
  jobFinished?: boolean
  jobSuccess?: boolean
}

function isCatalogPage(value: unknown): value is CatalogPage {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Partial<CatalogPage>
  return typeof candidate.total === 'number'
    && typeof candidate.offset === 'number'
    && Array.isArray(candidate.categories)
    && Array.isArray(candidate.entries)
}

function isInstallStatus(value: unknown): value is InstallStatus {
  if (typeof value !== 'object' || value === null) return false
  const candidate = value as Partial<InstallStatus>
  return typeof candidate.jobRunning === 'boolean'
    && Array.isArray(candidate.jobOutput)
}

const PAGE_SIZE = 30
const FEATURED_PLUGIN = 'dsh-market/dsh-market'

export function PluginMarket({ bridge }: PluginMarketProps) {
  const [query, setQuery] = useState('')
  const [debounced, setDebounced] = useState('')
  const [category, setCategory] = useState('')
  const [offset, setOffset] = useState(0)
  const [page, setPage] = useState<CatalogPage | null>(null)
  const [jobs, setJobs] = useState<Record<string, InstallStatus>>({})
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    if (searchTimer.current !== null) clearTimeout(searchTimer.current)
    searchTimer.current = setTimeout(() => {
      setDebounced(query.trim())
      setOffset(0)
    }, 260)
    return () => { if (searchTimer.current !== null) clearTimeout(searchTimer.current) }
  }, [query])

  const load = useCallback(async () => {
    setBusy(true)
    try {
      const reply = await bridge.requestV2<CatalogPage>('plugin.catalog.list', undefined, {
        query: debounced === '' ? undefined : debounced,
        category: category === '' ? undefined : category,
        offset,
        limit: PAGE_SIZE,
      })
      // 桥返回的页面形状异常时降级为错误状态，绝不让整个页签崩溃。
      if (!isCatalogPage(reply)) throw new Error('插件目录响应异常，请刷新重试')
      setPage(reply)
      setError(null)
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(false)
    }
  }, [bridge, debounced, category, offset])

  useEffect(() => { void load() }, [load])

  // 目录页到达后拉取本页插件的安装状态（页面重挂载后也能恢复“已安装”）。
  useEffect(() => {
    if (page === null) return
    let cancelled = false
    const ids = page.entries.map((entry) => entry.id)
    void (async () => {
      const next: Record<string, InstallStatus> = {}
      await Promise.all(ids.map(async (id) => {
        try {
          const status = await bridge.requestV2<InstallStatus>('plugin.install.status', undefined, { pluginId: id })
          if (isInstallStatus(status) && (status.jobFinished !== undefined || status.jobRunning)) next[id] = status
        } catch { /* 状态不可知时按未安装展示 */ }
      }))
      if (!cancelled && Object.keys(next).length > 0) setJobs((current) => ({ ...next, ...current }))
    })()
    return () => { cancelled = true }
  }, [bridge, page])

  const jobsRunning = useMemo(() => Object.values(jobs).some((job) => job.jobRunning), [jobs])

  const pollJobs = useCallback(async () => {
    const running = Object.entries(jobs).filter(([, job]) => job.jobRunning).map(([id]) => id)
    if (running.length === 0) return
    const next: Record<string, InstallStatus> = {}
    await Promise.all(running.map(async (id) => {
      try {
        const status = await bridge.requestV2<InstallStatus>('plugin.install.status', undefined, { pluginId: id })
        if (isInstallStatus(status)) next[id] = status
      } catch { /* 轮询失败下次再试 */ }
    }))
    if (Object.keys(next).length > 0) setJobs((current) => ({ ...current, ...next }))
  }, [bridge, jobs])

  useEffect(() => {
    if (!jobsRunning) return
    const timer = setInterval(() => { void pollJobs() }, 2000)
    return () => clearInterval(timer)
  }, [jobsRunning, pollJobs])

  const install = async (entry: CatalogEntry) => {
    try {
      const reply = await bridge.requestV2<InstallStatus>('plugin.install.start', undefined, { pluginId: entry.id })
      if (!isInstallStatus(reply)) throw new Error('安装响应异常，请重试')
      setJobs((current) => ({ ...current, [entry.id]: reply }))
    } catch (cause) {
      setError(messageOf(cause))
    }
  }

  const refreshStatus = async (entry: CatalogEntry) => {
    try {
      const reply = await bridge.requestV2<InstallStatus>('plugin.install.status', undefined, { pluginId: entry.id })
      setJobs((current) => ({ ...current, [entry.id]: reply }))
    } catch { /* ignore */ }
  }

  const featured = page?.categories !== undefined
    ? page.entries.find((entry) => entry.id === FEATURED_PLUGIN)
    : undefined

  return (
    <section className="dshPluginMarket" aria-label="插件市场">
      <header className="dshPluginMarketHead">
        <div>
          <h3>插件市场</h3>
          <p>{page?.total !== undefined ? `${page.total} 个社区插件` : '社区插件'} · 来自 awesome-dsh-plugin 精选目录 · 经官方 CLI 安装进当前 Profile</p>
        </div>
        <button type="button" className="dshAgentGhostButton" disabled={busy} onClick={() => { void load() }}>
          {busy ? '加载中…' : '刷新'}
        </button>
      </header>

      <div className="dshPluginMarketWarning" role="note">
        安装插件等于在你的机器上运行第三方代码，权限与你本人一样大。收录不代表安全审查——安装前请先看一眼源码。
      </div>

      {error !== null && <div className="dshModelAgentError" role="alert">{error}</div>}

      <div className="dshPluginMarketControls">
        <input
          type="search"
          value={query}
          placeholder="搜索插件：名称、分类或描述…"
          aria-label="搜索插件"
          onChange={(event) => setQuery(event.target.value)}
        />
        <div className="dshPluginMarketCategories" role="group" aria-label="分类筛选">
          <button
            type="button"
            className={category === '' ? 'is-active' : ''}
            onClick={() => { setCategory(''); setOffset(0) }}
          >
            全部{page?.total !== undefined && category === '' ? ` · ${page.total}` : ''}
          </button>
          {page?.categories.map((item) => (
            <button
              key={item.id}
              type="button"
              className={category === item.id ? 'is-active' : ''}
              onClick={() => { setCategory(item.id); setOffset(0) }}
            >
              {item.id} · {item.count}
            </button>
          ))}
        </div>
      </div>

      {featured !== undefined && offset === 0 && debounced === '' && (
        <article className="dshPluginCard is-featured" data-ready>
          <div className="dshPluginCardMain">
            <div className="dshPluginCardTitle">
              <strong>🛒 dsh-market —— 完整市场插件（推荐）</strong>
              <small>{featured.repo}</small>
            </div>
            <p>装上它之后，工作台设置页里就有完整市场：一键安装/升级插件、一键切换主题。</p>
          </div>
          <InstallButton entry={featured} job={jobs[featured.id]} onInstall={install} onRefresh={refreshStatus} />
        </article>
      )}

      <div className="dshPluginMarketGrid">
        {page?.entries.map((entry) => (
          <article key={entry.id} className="dshPluginCard">
            <div className="dshPluginCardMain">
              <div className="dshPluginCardTitle">
                <strong>{entry.displayName === '' ? entry.id : entry.displayName}</strong>
                <small><a href={entry.repo} target="_blank" rel="noreferrer">{entry.id}</a> · {entry.category}</small>
              </div>
              <p>{entry.descriptionZh !== '' ? entry.descriptionZh : entry.descriptionEn}</p>
            </div>
            <InstallButton entry={entry} job={jobs[entry.id]} onInstall={install} onRefresh={refreshStatus} />
          </article>
        ))}
      </div>

      {page !== null && page.entries.length === 0 && !busy && (
        <p className="dshModelAgentMuted">没有匹配的插件。换个关键词试试。</p>
      )}

      {page !== null && page.total > page.entries.length + page.offset && (
        <div className="dshPluginMarketMore">
          <button
            type="button"
            className="dshAgentGhostButton"
            disabled={busy}
            onClick={() => setOffset((current) => current + PAGE_SIZE)}
          >
            加载更多（还有 {page.total - page.offset - page.entries.length} 个）
          </button>
        </div>
      )}
    </section>
  )
}

function InstallButton(props: {
  entry: CatalogEntry
  job: InstallStatus | undefined
  onInstall(entry: CatalogEntry): void
  onRefresh(entry: CatalogEntry): void
}) {
  const { job } = props
  if (job === undefined) {
    return (
      <button type="button" className="dshAgentPrimaryButton" onClick={() => props.onInstall(props.entry)}>
        安装
      </button>
    )
  }
  if (job.jobRunning) {
    return (
      <button type="button" className="dshAgentGhostButton" disabled>
        安装中…
      </button>
    )
  }
  if (job.jobFinished === true && job.jobSuccess === true) {
    return (
      <span className="dshPluginInstalled" role="status">已安装 · 下次会话生效</span>
    )
  }
  return (
    <div className="dshPluginFailed">
      <button type="button" className="dshAgentGhostButton" onClick={() => props.onRefresh(props.entry)}>查看</button>
      <button type="button" className="dshAgentPrimaryButton" onClick={() => props.onInstall(props.entry)}>重试</button>
      {job.jobOutput.length > 0 && <pre className="dshPluginJobLog">{job.jobOutput.join('\n')}</pre>}
    </div>
  )
}
