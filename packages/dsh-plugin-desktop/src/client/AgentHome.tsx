import { useCallback, useEffect, useMemo, useState } from 'react'
import type { DesktopBridgeLike } from './desktop-bridge'
import { messageOf } from './model-agent/state'

/**
 * 主界面的 Agent 入口页：选择 Provider（Codex / Claude）并直达工作台。
 * Codex 卡片聚合 CLI 检测、安装、官方账号登录三步引导。
 */

export interface AgentHomeProps {
  bridge: DesktopBridgeLike
  workspaceId?: string
  onOpenWorkbench(providerId: string): void
}

interface ProviderSummary {
  providerId: string
  displayName: string
  credentialStatus?: string
}

interface LoginStatus {
  installed: boolean
  cliPath?: string
  loggedIn?: boolean
  mode?: string
  detail?: string
  jobRunning: boolean
  jobOutput: string[]
  jobFinished?: boolean
  jobSuccess?: boolean
}

interface InstallStatus {
  command: string[]
  sourceUrl?: string
  impact?: string
  installed: boolean
  jobRunning: boolean
  jobOutput: string[]
  jobFinished?: boolean
  jobSuccess?: boolean
}

export function AgentHome({ bridge, workspaceId, onOpenWorkbench }: AgentHomeProps) {
  const [providers, setProviders] = useState<ProviderSummary[]>([])
  const [login, setLogin] = useState<Record<string, LoginStatus>>({})
  const [install, setInstall] = useState<Record<string, InstallStatus>>({})
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    setBusy(true)
    try {
      const providerReply = await bridge.requestV2<ProviderSummary[]>('provider.metadata.list')
      setProviders(providerReply)
      const nextLogin: Record<string, LoginStatus> = {}
      const nextInstall: Record<string, InstallStatus> = {}
      await Promise.all(providerReply.map(async (provider) => {
        try {
          const [loginReply, installReply] = await Promise.all([
            bridge.requestV2<LoginStatus>('cli.login.status', undefined, { providerId: provider.providerId }),
            bridge.requestV2<InstallStatus>('cli.install.status', undefined, { providerId: provider.providerId }),
          ])
          nextLogin[provider.providerId] = loginReply
          nextInstall[provider.providerId] = installReply
        } catch (cause) {
          nextLogin[provider.providerId] = { installed: false, jobRunning: false, jobOutput: [] }
        }
      }))
      setLogin(nextLogin)
      setInstall(nextInstall)
      setError(null)
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(false)
    }
  }, [bridge])

  useEffect(() => { void load() }, [load])

  const refreshWhenJobsRun = useMemo(() => {
    const running = Object.values(login).some((status) => status.jobRunning)
      || Object.values(install).some((status) => status.jobRunning)
    return running
  }, [login, install])

  useEffect(() => {
    if (!refreshWhenJobsRun) return
    const timer = setInterval(() => { void load() }, 2000)
    return () => clearInterval(timer)
  }, [refreshWhenJobsRun, load])

  const startInstall = async (provider: ProviderSummary) => {
    try {
      const reply = await bridge.requestV2<InstallStatus>('cli.install.start', undefined, { providerId: provider.providerId })
      setInstall((current) => ({ ...current, [provider.providerId]: reply }))
    } catch (cause) {
      setError(messageOf(cause))
    }
  }

  const startLogin = async (provider: ProviderSummary) => {
    try {
      const reply = await bridge.requestV2<LoginStatus>('cli.login.start', undefined, { providerId: provider.providerId })
      setLogin((current) => ({ ...current, [provider.providerId]: reply }))
    } catch (cause) {
      setError(messageOf(cause))
    }
  }

  return (
    <section className="dshAgentHome" aria-label="Agent 入口">
      <header className="dshAgentHomeHeader">
        <div>
          <p className="dshModelAgentEyebrow">AGENT CONSOLE</p>
          <h2>选择你的 Agent</h2>
          <p>在当前工作区使用 Codex 等官方 CLI Agent 执行任务；首次使用先完成安装与登录。</p>
        </div>
        <button type="button" disabled={busy} onClick={() => void load()}>刷新</button>
      </header>
      {error !== null && <div className="dshModelAgentError" role="alert">{error}</div>}
      {workspaceId === undefined || workspaceId.trim() === ''
        ? <p className="dshModelAgentMuted">还没有可用工作区：先在左侧创建或选择一个项目。</p>
        : null}
      <div className="dshAgentHomeGrid">
        {providers.map((provider) => {
          const loginStatus = login[provider.providerId]
          const installStatus = install[provider.providerId]
          const codexReady = provider.providerId === 'codex'
            && loginStatus?.installed === true
            && loginStatus?.loggedIn !== false
          const ready = provider.providerId === 'codex' ? codexReady : loginStatus?.installed === true
          return (
            <article key={provider.providerId} className={`dshAgentHomeCard${provider.providerId === 'codex' ? ' is-codex' : ''}`} data-ready={ready || undefined}>
              <div className="dshAgentHomeCardHeader">
                <div><span className="dshAgentHomeMark">{provider.displayName.slice(0, 1)}</span><div><h3>{provider.displayName}</h3><small>{provider.providerId}:default</small></div></div>
                <span className={`dshModelAgentStatus dshModelAgentStatus-${ready ? 'available' : 'missing-cli'}`}>{ready ? '可用' : '待准备'}</span>
              </div>
              <dl className="dshModelAgentDetails">
                <div><dt>CLI</dt><dd title={loginStatus?.cliPath ?? ''}>{loginStatus?.installed === true ? (loginStatus.cliPath ?? '已安装') : '未安装'}</dd></div>
                <div><dt>账号</dt><dd>{provider.providerId === 'codex' ? (loginStatus?.mode ?? (loginStatus?.loggedIn === true ? '已登录' : '未登录')) : '—'}</dd></div>
                <div><dt>凭证</dt><dd>{provider.credentialStatus === 'configured' ? '已配置' : '未配置'}</dd></div>
              </dl>
              {provider.providerId === 'codex' && (
                <div className="dshAgentHomeActions">
                  {loginStatus?.installed !== true && (
                    <button type="button" disabled={installStatus?.jobRunning === true} onClick={() => void startInstall(provider)}>
                      {installStatus?.jobRunning === true ? '正在安装…' : '安装 Codex CLI'}
                    </button>
                  )}
                  {loginStatus?.installed === true && loginStatus.loggedIn !== true && (
                    <button type="button" disabled={loginStatus.jobRunning} onClick={() => void startLogin(provider)}>
                      {loginStatus.jobRunning ? '等待浏览器授权…' : '登录官方账号'}
                    </button>
                  )}
                  <button type="button" className="dshModelAgentPrimary" disabled={!ready || workspaceId === undefined || workspaceId.trim() === ''} onClick={() => onOpenWorkbench(provider.providerId)}>
                    进入 {provider.displayName} 工作台
                  </button>
                </div>
              )}
              {provider.providerId !== 'codex' && (
                <div className="dshAgentHomeActions">
                  <button type="button" disabled onClick={() => onOpenWorkbench(provider.providerId)}>即将支持</button>
                </div>
              )}
              {installStatus?.jobOutput !== undefined && installStatus.jobOutput.length > 0 && (
                <pre className="dshAgentHomeJobOutput">{installStatus.jobOutput.join('\n')}</pre>
              )}
              {loginStatus?.jobOutput !== undefined && loginStatus.jobOutput.length > 0 && (
                <pre className="dshAgentHomeJobOutput">{loginStatus.jobOutput.join('\n')}</pre>
              )}
            </article>
          )
        })}
      </div>
    </section>
  )
}
