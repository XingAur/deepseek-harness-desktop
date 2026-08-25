import { useCallback, useEffect, useMemo, useState } from 'react'
import type { DesktopBridgeLike } from './desktop-bridge'
import { messageOf } from './model-agent/state'

/**
 * 主界面 Agent 入口页。
 *
 * 设计目标：一眼看懂「我在哪一步、下一步做什么」。
 * - 顶部：一句话说明 + 重新检测
 * - Codex 卡片：三步就绪状态（安装 CLI → 登录账号 → 选择工作区），
 *   每一步都有明确的完成/待办状态和人话提示
 * - 高级：手动指定 CLI 路径（自动检测失败时的自助兜底）
 * - 任务日志：仅在有内容时展示，终端风格
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

type StepState = 'done' | 'active' | 'todo'

export function AgentHome({ bridge, workspaceId, onOpenWorkbench }: AgentHomeProps) {
  const [providers, setProviders] = useState<ProviderSummary[]>([])
  const [login, setLogin] = useState<Record<string, LoginStatus>>({})
  const [install, setInstall] = useState<Record<string, InstallStatus>>({})
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [manualPath, setManualPath] = useState('')
  const [manualBusy, setManualBusy] = useState(false)
  const [manualResult, setManualResult] = useState<string | null>(null)

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

  const jobsRunning = useMemo(
    () => Object.values(login).some((status) => status.jobRunning)
      || Object.values(install).some((status) => status.jobRunning),
    [login, install],
  )

  useEffect(() => {
    if (!jobsRunning) return
    const timer = setInterval(() => { void load() }, 2000)
    return () => clearInterval(timer)
  }, [jobsRunning, load])

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

  const saveManualPath = async () => {
    const trimmed = manualPath.trim()
    if (trimmed.length === 0 || manualBusy) return
    setManualBusy(true)
    setManualResult(null)
    try {
      await bridge.requestV2('cli.path.select', undefined, { providerId: 'codex', path: trimmed })
      setManualResult('已保存，并且检测通过。现在可以直接进入工作台。')
      await load()
    } catch (cause) {
      setManualResult(messageOf(cause))
    } finally {
      setManualBusy(false)
    }
  }

  return (
    <section className="dshAgentHome" aria-label="Agent 入口">
      <header className="dshAgentHomeHero">
        <div className="dshAgentHomeHeroCopy">
          <h2>Agent</h2>
          <p>选择一个 Agent，在你的项目里执行真实任务。Codex 由官方 CLI 驱动，写入文件、执行命令等操作都会先请求你的批准。</p>
        </div>
        <button type="button" className="dshAgentGhostButton" disabled={busy} onClick={() => { void load() }}>
          {busy ? '检测中…' : '重新检测'}
        </button>
      </header>

      {error !== null && <div className="dshModelAgentError" role="alert">{error}</div>}

      <div className="dshAgentHomeGrid">
        {providers.filter((provider) => provider.providerId === 'codex').map((provider) => {
          const loginStatus = login[provider.providerId]
          const installStatus = install[provider.providerId]
          const installJob = installStatus?.jobRunning === true
          const loginJob = loginStatus?.jobRunning === true
          const installed = loginStatus?.installed === true
          const loggedIn = loginStatus?.loggedIn === true
          const workspaceReady = workspaceId !== undefined && workspaceId.trim() !== ''
          const ready = installed && loggedIn && workspaceReady
          const installStep: StepState = installJob ? 'active' : installed ? 'done' : 'todo'
          const loginStep: StepState = !installed ? 'todo' : loginJob ? 'active' : loggedIn ? 'done' : 'active'
          const workspaceStep: StepState = installed && loggedIn ? (workspaceReady ? 'done' : 'active') : 'todo'
          const logLines = [...(installStatus?.jobOutput ?? []), ...(loginStatus?.jobOutput ?? [])]
          return (
            <article key={provider.providerId} className="dshAgentCard is-codex" data-ready={ready || undefined}>
              <header className="dshAgentCardHead">
                <div className="dshAgentCardIdentity">
                  <span className="dshAgentCardGlyph" aria-hidden="true">
                    <svg viewBox="0 0 24 24" fill="none"><path d="M12 3a9 9 0 1 0 9 9" /><circle cx="12" cy="12" r="3.2" /><path d="M12 3v3.5M21 12h-3.5" /></svg>
                  </span>
                  <div>
                    <h3>Codex</h3>
                    <small>OpenAI 官方 CLI Agent</small>
                  </div>
                </div>
                <span className={`dshAgentPill ${ready ? 'is-ok' : 'is-warn'}`}>{ready ? '可用' : '待准备'}</span>
              </header>

              <ol className="dshAgentSteps" aria-label="准备进度">
                <Step state={installStep} index={1} title="安装 Codex CLI"
                  doneText={loginStatus?.cliPath || '已在本机检测到'}
                  todoText={installJob ? '正在安装，通常需要 1-2 分钟，完成后自动检测。' : '还没有安装。点下方「安装 Codex CLI」，通过 npm 安装官方命令行。'} />
                <Step state={loginStep} index={2} title="登录官方账号"
                  doneText={loginStatus?.mode || '已登录'}
                  todoText={loginJob ? '已打开浏览器。完成 ChatGPT 授权后回到这里，状态会自动刷新。' : 'CLI 已就绪。还需要用你的 OpenAI 账号登录一次，登录只在浏览器里进行。'} />
                <Step state={workspaceStep} index={3} title="选择工作区"
                  doneText={workspaceId ?? ''}
                  todoText="在左侧打开或创建一个项目，这里会自动就绪。" />
              </ol>

              <div className="dshAgentCardActions">
                <button
                  type="button"
                  className="dshAgentPrimaryButton"
                  disabled={!ready}
                  onClick={() => onOpenWorkbench(provider.providerId)}
                >
                  进入 Codex 工作台
                </button>
                {!installed && !installJob && (
                  <button type="button" className="dshAgentGhostButton" onClick={() => void startInstall(provider)}>
                    安装 Codex CLI
                  </button>
                )}
                {installed && !loggedIn && !loginJob && (
                  <button type="button" className="dshAgentGhostButton" onClick={() => void startLogin(provider)}>
                    登录官方账号
                  </button>
                )}
              </div>

              {logLines.length > 0 && (
                <div className="dshAgentLog">
                  <div className="dshAgentLogHead">
                    <span>{installJob ? '安装日志' : loginJob ? '登录日志' : '日志'}</span>
                    {jobsRunning ? <span className="dshAgentLogLive">进行中</span> : null}
                  </div>
                  <pre>{logLines.join('\n')}</pre>
                </div>
              )}

              <details className="dshAgentAdvanced">
                <summary>高级：自动检测不到 CLI？手动指定路径</summary>
                <div className="dshAgentAdvancedBody">
                  <p>在终端里运行 <code>which codex</code>，把输出的完整路径粘贴到这里。保存后会立即验证并生效。</p>
                  <div className="dshAgentAdvancedRow">
                    <input
                      type="text"
                      value={manualPath}
                      placeholder="/opt/homebrew/bin/codex"
                      spellCheck={false}
                      disabled={manualBusy}
                      onChange={(event) => setManualPath(event.target.value)}
                      onKeyDown={(event) => { if (event.key === 'Enter') void saveManualPath() }}
                    />
                    <button type="button" className="dshAgentGhostButton" disabled={manualBusy || manualPath.trim() === ''} onClick={() => void saveManualPath()}>
                      {manualBusy ? '验证中…' : '保存并检测'}
                    </button>
                  </div>
                  {manualResult !== null && <p className="dshAgentAdvancedResult" role="status">{manualResult}</p>}
                </div>
              </details>
            </article>
          )
        })}

        {providers.filter((provider) => provider.providerId !== 'codex').map((provider) => (
          <article key={provider.providerId} className="dshAgentCard is-coming" aria-disabled="true">
            <header className="dshAgentCardHead">
              <div className="dshAgentCardIdentity">
                <span className="dshAgentCardGlyph" aria-hidden="true">
                  <svg viewBox="0 0 24 24" fill="none"><path d="M12 4v16M4 12h16" /></svg>
                </span>
                <div>
                  <h3>{provider.displayName}</h3>
                  <small>官方 CLI Agent</small>
                </div>
              </div>
              <span className="dshAgentPill is-muted">即将支持</span>
            </header>
            <p className="dshAgentComingHint">这个 Provider 正在准备中。当前版本先把 Codex 做扎实。</p>
          </article>
        ))}
      </div>
    </section>
  )
}

function Step(props: { state: StepState; index: number; title: string; doneText: string; todoText: string }) {
  return (
    <li className="dshAgentStep" data-state={props.state}>
      <span className="dshAgentStepMark" aria-hidden="true">
        {props.state === 'done'
          ? <svg viewBox="0 0 24 24" fill="none"><path d="m5 12.5 4.5 4.5L19 7.5" /></svg>
          : props.state === 'active'
            ? <span className="dshAgentStepPulse" />
            : props.index}
      </span>
      <div className="dshAgentStepCopy">
        <strong>{props.title}</strong>
        <p>{props.state === 'done' ? props.doneText : props.todoText}</p>
      </div>
    </li>
  )
}
