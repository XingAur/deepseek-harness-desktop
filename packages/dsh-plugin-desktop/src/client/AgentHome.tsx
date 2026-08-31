import { useCallback, useEffect, useMemo, useState } from 'react'
import type { DesktopBridgeLike } from './desktop-bridge'
import { messageOf } from './model-agent/state'

/**
 * 主界面 Agent 入口（仅 Codex：当前唯一接通真实执行的 Provider）。
 *
 * 目标：一眼看懂「现在到哪一步、下一步做什么」，任何失败都给出人话
 * 原因和下一步，绝不出现内部术语。
 */

export interface AgentHomeProps {
  bridge: DesktopBridgeLike
  workspaceId?: string
  embedded?: boolean
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
  installed: boolean
  jobRunning: boolean
  jobOutput: string[]
  jobFinished?: boolean
  jobSuccess?: boolean
}

type StepState = 'done' | 'active' | 'todo'

export function AgentHome({ bridge, workspaceId, embedded = false }: AgentHomeProps) {
  const [login, setLogin] = useState<LoginStatus | null>(null)
  const [install, setInstall] = useState<InstallStatus | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [manualPath, setManualPath] = useState('')
  const [manualBusy, setManualBusy] = useState(false)
  const [manualResult, setManualResult] = useState<string | null>(null)

  const load = useCallback(async () => {
    setBusy(true)
    try {
      const [loginReply, installReply] = await Promise.all([
        bridge.requestV2<LoginStatus>('cli.login.status', undefined, { providerId: 'codex' }),
        bridge.requestV2<InstallStatus>('cli.install.status', undefined, { providerId: 'codex' }),
      ])
      setLogin(loginReply)
      setInstall(installReply)
      setError(null)
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(false)
    }
  }, [bridge])

  useEffect(() => { void load() }, [load])

  const jobsRunning = useMemo(
    () => login?.jobRunning === true || install?.jobRunning === true,
    [login, install],
  )
  useEffect(() => {
    if (!jobsRunning) return
    const timer = setInterval(() => { void load() }, 2000)
    return () => clearInterval(timer)
  }, [jobsRunning, load])

  const startInstall = async () => {
    try {
      setInstall(await bridge.requestV2<InstallStatus>('cli.install.start', undefined, { providerId: 'codex' }))
    } catch (cause) {
      setError(messageOf(cause))
    }
  }

  const startLogin = async () => {
    try {
      setLogin(await bridge.requestV2<LoginStatus>('cli.login.start', undefined, { providerId: 'codex' }))
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
      setManualResult('已保存并检测通过，可以直接进入工作台。')
      await load()
    } catch (cause) {
      setManualResult(messageOf(cause))
    } finally {
      setManualBusy(false)
    }
  }

  const installJob = install?.jobRunning === true
  const loginJob = login?.jobRunning === true
  const installed = login?.installed === true
  const loggedIn = login?.loggedIn === true
  const loginUnknown = installed === true && login?.loggedIn === undefined
  const loginFailed = installed === true && login?.loggedIn === false
  const workspaceReady = workspaceId !== undefined && workspaceId.trim() !== ''
  const ready = installed && loggedIn && workspaceReady
  void ready

  const installStep: StepState = installJob ? 'active' : installed ? 'done' : 'todo'
  const loginStep: StepState = !installed ? 'todo' : loginJob || loginUnknown ? 'active' : loggedIn ? 'done' : 'active'
  const workspaceStep: StepState = installed && loggedIn ? (workspaceReady ? 'done' : 'active') : 'todo'

  const jobLog = [...(install?.jobOutput ?? []), ...(login?.jobOutput ?? [])]

  return (
    <section className="dshAgentHome" aria-label="Agent" data-embedded={embedded || undefined}>
      <header className="dshAgentHomeHero">
        {embedded ? <h3>Codex 执行器</h3> : <h2>Agent</h2>}
        <button type="button" className="dshAgentGhostButton" disabled={busy} onClick={() => { void load() }}>
          {busy ? '检测中…' : '重新检测'}
        </button>
      </header>
      <p className="dshAgentHomeLead">
        Codex 在你的项目里真实执行任务；写文件、跑命令等操作都会先请求你的批准。
      </p>

      {error !== null && <div className="dshModelAgentError" role="alert">{error}</div>}

      <article className="dshAgentCard" data-ready={ready || undefined}>
        <div className="dshAgentCardHead">
          <h3>Codex</h3>
          <span className={`dshAgentPill ${ready ? 'is-ok' : 'is-warn'}`}>{ready ? '可用' : '待准备'}</span>
        </div>

        <ol className="dshAgentSteps">
          <li className="dshAgentStep" data-state={installStep}>
            <StepMark state={installStep} index={1} />
            <div>
              <strong>安装 Codex CLI</strong>
              <p>
                {installJob ? '正在安装，通常 1-2 分钟，完成后自动检测。'
                  : installed ? (login?.cliPath ?? '已在本机检测到')
                    : '还没有安装。点下方按钮，通过 npm 安装官方命令行。'}
              </p>
            </div>
          </li>
          <li className="dshAgentStep" data-state={loginStep}>
            <StepMark state={loginStep} index={2} />
            <div>
              <strong>登录官方账号</strong>
              <p>
                {loginJob ? '已打开浏览器，完成 ChatGPT 授权后回到这里，状态会自动刷新。'
                  : loginUnknown ? '正在确认登录状态…'
                    : loggedIn ? (login?.mode || '已登录')
                      : installed ? 'CLI 已就绪，用 OpenAI 账号登录一次即可（只在浏览器里进行）。'
                        : '先完成上一步。'}
              </p>
            </div>
          </li>
          <li className="dshAgentStep" data-state={workspaceStep}>
            <StepMark state={workspaceStep} index={3} />
            <div>
              <strong>选择工作区</strong>
              <p>{installed && loggedIn ? (workspaceReady ? workspaceId : '在左侧打开或创建一个项目；聊天会话所在的目录就是 Codex 的工作目录。') : '先完成前两步。'}</p>
            </div>
          </li>
        </ol>

        {loginFailed && !loginJob && login?.detail !== undefined && (
          <p className="dshAgentCardNote">{login.detail}</p>
        )}

        <div className="dshAgentCardActions">
          {ready
            ? <p className="dshAgentReadyNote">已就绪：回到主聊天，在输入框上方的模型选择器里选择 <strong>Codex</strong> 即可开始。</p>
            : <p className="dshAgentReadyNote">准备好之后，在主聊天的模型选择器里就会出现 Codex。</p>}
          {!installed && !installJob && (
            <button type="button" className="dshAgentPrimaryButton" onClick={() => { void startInstall() }}>安装 Codex CLI</button>
          )}
          {loginFailed && !loginJob && (
            <button type="button" className="dshAgentGhostButton" onClick={() => { void startLogin() }}>登录官方账号</button>
          )}
        </div>

        {jobLog.length > 0 && (
          <details className="dshAgentLog" open={jobsRunning}>
            <summary>{installJob ? '安装日志' : loginJob ? '登录日志' : '日志'}</summary>
            <pre>{jobLog.join('\n')}</pre>
          </details>
        )}

        <details className="dshAgentAdvanced">
          <summary>高级：自动检测不到 CLI？手动指定路径</summary>
          <div className="dshAgentAdvancedBody">
            <p>在终端里运行 <code>which codex</code>，把输出的完整路径粘贴到这里。保存后立即验证并生效。</p>
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
              <button type="button" className="dshAgentGhostButton" disabled={manualBusy || manualPath.trim() === ''} onClick={() => { void saveManualPath() }}>
                {manualBusy ? '验证中…' : '保存并检测'}
              </button>
            </div>
            {manualResult !== null && <p className="dshAgentAdvancedResult" role="status">{manualResult}</p>}
          </div>
        </details>
      </article>
    </section>
  )
}

function StepMark(props: { state: StepState; index: number }) {
  if (props.state === 'done') {
    return (
      <span className="dshAgentStepMark" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none"><path d="m5 12.5 4.5 4.5L19 7.5" /></svg>
      </span>
    )
  }
  if (props.state === 'active') {
    return <span className="dshAgentStepMark is-active" aria-hidden="true"><span className="dshAgentStepPulse" /></span>
  }
  return <span className="dshAgentStepMark" aria-hidden="true">{props.index}</span>
}
