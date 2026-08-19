import { useEffect, useMemo, useRef, useState } from 'react'
import { marketClient, type CommunityPlugin, type PluginAction, type PluginOperationEvent } from './market-client'

export function MarketPage({ onClose }: { onClose(): void }) {
  const [plugins, setPlugins] = useState<CommunityPlugin[]>([])
  const [query, setQuery] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [active, setActive] = useState<{ pluginId: string; operationId: string; logs: string[] } | null>(null)
  // 页面卸载时必须断开操作日志订阅，避免 EventSource 与 setState 泄漏。
  const stopEvents = useRef<(() => void) | null>(null)
  useEffect(() => () => stopEvents.current?.(), [])

  const refresh = async () => {
    setLoading(true)
    try { setPlugins(await marketClient.list()); setError(null) }
    catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
    finally { setLoading(false) }
  }
  useEffect(() => { void refresh() }, [])

  const visible = useMemo(() => {
    const term = query.trim().toLocaleLowerCase()
    return term === '' ? plugins : plugins.filter((plugin) => `${plugin.name} ${plugin.description} ${plugin.publisher}`.toLocaleLowerCase().includes(term))
  }, [plugins, query])

  const execute = async (plugin: CommunityPlugin, action: PluginAction) => {
    const label = action === 'remove' ? '卸载' : action === 'update' ? '更新' : '安装'
    if (!window.confirm(`${label}第三方插件「${plugin.name}」？\n\n来源：${plugin.repository}\n第三方插件会获得 DeepSeek Harness 插件体系允许的能力。`)) return
    try {
      const preview = await marketClient.preview(plugin.id, action)
      const operation = await marketClient.execute(preview.token)
      setActive({ pluginId: plugin.id, operationId: operation.operationId, logs: [] })
      stopEvents.current?.()
      stopEvents.current = marketClient.events(operation.operationId, (event: PluginOperationEvent) => {
        setActive((current) => current?.operationId === operation.operationId
          ? { ...current, logs: [...current.logs, event.line].slice(-120) }
          : current)
        if (event.done) {
          stopEvents.current?.()
          stopEvents.current = null
          void refresh()
          setTimeout(() => setActive(null), 900)
        }
      })
    } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)) }
  }

  return (
    <section className="marketPage">
      <header className="marketHeader">
        <div><p>DEEPSEEK HARNESS DESKTOP</p><h1>社区插件</h1><span>经过校验的精选插件；GitHub Topic 仅用于候选发现。</span></div>
        <button onClick={onClose} aria-label="关闭社区插件">×</button>
      </header>
      <div className="marketToolbar"><input aria-label="搜索插件" placeholder="搜索插件、发布者或能力" value={query} onChange={(event) => setQuery(event.target.value)} /><button onClick={() => void refresh()}>刷新</button></div>
      {error && <div className="marketError" role="alert">{error}</div>}
      {loading ? <div className="marketEmpty">正在读取签名目录…</div> : visible.length === 0 ? <div className="marketEmpty">没有匹配的精选插件</div> : (
        <div className="marketGrid">{visible.map((plugin) => {
          const busy = active?.pluginId === plugin.id
          const operationRunning = active !== null
          return <article className="marketCard" key={plugin.id}>
            <div className="marketCardTitle"><h2>{plugin.name}</h2>{plugin.verified && <span>已验证</span>}</div>
            <p>{plugin.description}</p><small>{plugin.publisher} · {plugin.version}</small>
            <div className="marketActions">
              {!plugin.installed && <button disabled={operationRunning} onClick={() => void execute(plugin, 'install')}>{busy ? '处理中…' : '安装'}</button>}
              {plugin.installed && plugin.updateAvailable && <button disabled={operationRunning} onClick={() => void execute(plugin, 'update')}>{busy ? '处理中…' : '更新'}</button>}
              {plugin.installed && <button className="marketRemove" disabled={operationRunning} onClick={() => void execute(plugin, 'remove')}>{busy ? '处理中…' : '卸载'}</button>}
            </div>
            {busy && <><pre className="marketLogs">{active.logs.join('\n') || '正在启动操作…'}</pre><button className="marketCancel" onClick={() => void marketClient.cancel(active.operationId)}>取消</button></>}
          </article>
        })}</div>
      )}
    </section>
  )
}
