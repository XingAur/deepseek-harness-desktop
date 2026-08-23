import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import type { AppUpdateFailure, AppUpdateReceipt, AppUpdateState, GenerationPhase, LocalAppEvent, MigrationStatus, RecoveryStatus, RuntimeClient, RuntimeFailure, RuntimeFailureCode, RuntimePhase } from './runtime-contract'
import { failureFromUnknown, initialRuntimeState, runtimeReducer } from './runtime-reducer'
import { themeFromWorkbenchMessage } from './theme-message'
import type { DesktopColorScheme } from './theme-message'
import { TitleBar } from './TitleBar'
import { DeepSeekFishLogo } from './DeepSeekFishLogo'
import type { WindowControls } from './window-client'
import { MigrationPrompt } from './MigrationPrompt'
import { invoke } from '@tauri-apps/api/core'
import { createWorkbenchBridge } from './workbench-bridge'

const phaseLabels: Record<RuntimePhase | GenerationPhase, string> = {
  checking: '检查环境',
  'fetching-manifest': '检查更新',
  downloading: '下载组件',
  extracting: '正在准备',
  verifying: '校验文件',
  activating: '安装组件',
  starting: '启动应用',
  ready: '准备完成',
  cancelled: '已取消',
  failed: '出现问题',
  idle: '等待启动',
  'resolving-profile': '载入 Profile',
  'preparing-runtime': '准备 Runtime',
  probing: '检查运行环境',
  active: '准备完成',
  draining: '安全退出',
  stopped: '已停止',
}

const failureCopy: Record<RuntimeFailureCode, string> = {
  network: '网络连接不可用或太慢。请检查网络后重试。',
  signature: '下载的文件未通过安全校验，已停止安装。请重试；若反复出现，请导出诊断并反馈。',
  archive: '安装包似乎已损坏。请点击「修复 DeepSeek Harness」重新下载。',
  process: 'DeepSeek Harness 意外退出，未能完成启动。请重试或修复。',
  'health-timeout': '启动等待超时。请重试；若持续出现，请导出诊断并反馈。',
  'migration-conflict': '检测到两个都有内容的数据目录，已停止自动迁移。请保留数据并选择要继续使用的目录。',
  'repair-required': '本地运行组件的安装记录不完整。请点击「修复 DeepSeek Harness」重新安装。',
  cancelled: '本次启动已取消。点击重试即可重新开始。',
  internal: '程序内部出现了一点问题。请重试；若持续出现，请导出诊断并反馈。',
}

const backupEvidenceLostMessage = 'Agent 数据库恢复证据已丢失，已阻止启动'

export interface AppProps {
  runtime: RuntimeClient
  windowControls: WindowControls
}

function updateFailureFromUnknown(cause: unknown): AppUpdateFailure {
  if (typeof cause === 'object' && cause !== null) {
    const candidate = cause as Partial<AppUpdateFailure>
    if (typeof candidate.message === 'string') {
      return { code: candidate.code ?? 'update', message: candidate.message, recoverable: candidate.recoverable ?? true }
    }
  }
  return { code: 'update', message: cause instanceof Error ? cause.message : String(cause), recoverable: true }
}

function formatUpdateSize(bytes: number | null) {
  if (bytes === null || bytes <= 0) return null
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function workbenchUrl(rendererUrl: string): string {
  try {
    const url = new URL(rendererUrl)
    url.searchParams.set('dsh-desktop-parent-origin', window.location.origin)
    return url.toString()
  } catch {
    return rendererUrl
  }
}

export interface AppUpdateBannerProps {
  state: AppUpdateState
  receipt: AppUpdateReceipt | null
  installOnExit: boolean
  diagnosticPath: string | null
  onDownload(): void
  onInstallNow(): void
  onInstallOnExit(): void
  onDefer(): void
  onOpenManual(): void
  onRetry(): void
  onDismissReceipt(): void
  onExportDiagnostics(): void
}

export function AppUpdateBanner(props: AppUpdateBannerProps) {
  const { state, receipt } = props
  if (state.phase === 'idle') {
    if (receipt === null) return null
    return (
      <aside className="appUpdateBanner success" role="status">
        <div className="appUpdateCopy">
          <strong>DeepSeek Harness 已更新</strong>
          <span>{receipt.previousVersion} → {receipt.targetVersion}</span>
        </div>
        <button className="updateTextButton" onClick={props.onDismissReceipt}>知道了</button>
      </aside>
    )
  }

  if (state.phase === 'checking') return null

  if (state.phase === 'failed') {
    return (
      <aside className="appUpdateBanner error" role="status">
        <div className="appUpdateCopy">
          <strong>应用更新暂时不可用</strong>
          <span>{state.update.message}，不影响当前工作台。</span>
          {props.diagnosticPath !== null && <small>诊断已保存：{props.diagnosticPath}</small>}
        </div>
        <div className="appUpdateActions">
          <button className="updatePrimaryButton" onClick={props.onRetry}>重试</button>
          <button className="updateTextButton" onClick={props.onExportDiagnostics}>导出诊断</button>
          <button className="updateTextButton" onClick={props.onDefer}>关闭</button>
        </div>
      </aside>
    )
  }

  const size = formatUpdateSize(state.update.size)
  const copy = state.update.notes?.trim() || '包含体验与稳定性改进'
  const manualDmg = state.update.mode === 'manual-dmg'
  const title = state.phase === 'available'
    ? `发现 DeepSeek Harness ${state.update.version}`
    : state.phase === 'downloading'
      ? `正在后台下载 ${state.update.version}`
      : state.phase === 'ready'
        ? `DeepSeek Harness ${state.update.version} 已准备好`
        : state.phase === 'installing'
          ? `正在安装 ${state.update.version}`
          : `正在重启到 ${state.update.version}`

  return (
    <aside className={`appUpdateBanner${manualDmg ? ' manual' : ''}`} role="status">
      <div className="appUpdateCopy">
        <strong>{title}</strong>
        <span>{copy}{size !== null ? ` · ${size}` : ''}</span>
        {manualDmg && (
          <>
            <small className="manualUpdateWarning">
              此 DMG 未使用 Apple Developer ID 签名、未经过 Apple 公证。Profile、项目和本地数据不会随应用替换而删除。
            </small>
            <small className="manualUpdateSteps">
              操作：退出本应用，打开 DMG，将新应用拖入“应用程序”并确认替换。不要删除 Application Support 中的数据。若被拦截，请在 Finder 中按住 Control 点按应用并选择“打开”，或到“系统设置 → 隐私与安全性”确认打开。
            </small>
          </>
        )}
        {props.installOnExit && state.phase === 'ready' && <small>已安排在退出应用时安装</small>}
      </div>
      <div className="appUpdateActions">
        {manualDmg && state.phase === 'available' && <button className="updatePrimaryButton" onClick={props.onOpenManual}>下载 DMG</button>}
        {!manualDmg && state.phase === 'available' && <button className="updatePrimaryButton" onClick={props.onDownload}>后台下载</button>}
        {!manualDmg && state.phase === 'ready' && <button className="updatePrimaryButton" onClick={props.onInstallNow}>立即重启安装</button>}
        {!manualDmg && state.phase === 'ready' && <button className="updateSecondaryButton" disabled={props.installOnExit} onClick={props.onInstallOnExit}>退出时安装</button>}
        {(state.phase === 'available' || state.phase === 'ready') && <button className="updateTextButton" onClick={props.onDefer}>{manualDmg ? '稍后提醒' : '暂不安装'}</button>}
        {(state.phase === 'downloading' || state.phase === 'installing' || state.phase === 'restarting') && <span className="updateSpinner" aria-label="处理中" />}
      </div>
    </aside>
  )
}

export function App({ runtime, windowControls }: AppProps) {
  const [state, dispatch] = useReducer(runtimeReducer, initialRuntimeState)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [shellTheme, setShellTheme] = useState<DesktopColorScheme | undefined>()
  const [migration, setMigration] = useState<MigrationStatus | null>(null)
  const [recovery, setRecovery] = useState<RecoveryStatus | null>(null)
  const [appUpdate, setAppUpdate] = useState<AppUpdateState>({ phase: 'idle' })
  const [appUpdateReceipt, setAppUpdateReceipt] = useState<AppUpdateReceipt | null>(null)
  const [installOnExit, setInstallOnExit] = useState(false)
  const [updateDiagnosticPath, setUpdateDiagnosticPath] = useState<string | null>(null)
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const [activeApp, setActiveApp] = useState<{ workspaceId: string; origin: string; title: string } | null>(null)
  const startupUpdate = useRef<{
    runtime: RuntimeClient
    receipt: Promise<AppUpdateReceipt | null>
    check: Promise<AppUpdateState>
  } | null>(null)
  const manualUpdateSeen = useRef(false)

  const start = useCallback(async (repair: boolean) => {
    try {
      const reply = repair ? await runtime.repairRuntime() : await runtime.bootstrapRuntime()
      dispatch({ type: 'bootstrap-started', reply })
    } catch (cause) {
      dispatch({ type: 'request-failed', error: failureFromUnknown(cause) })
    }
  }, [runtime])

  useEffect(() => {
    let disposed = false
    const unsubscribes: (() => void)[] = []
    void runtime.subscribeRuntimeProgress((event) => {
      if (!disposed) dispatch({ type: 'runtime-event', event })
    }).then((off) => {
      if (disposed) off()
      else unsubscribes.push(off)
    }).catch((cause) => {
      if (!disposed) dispatch({ type: 'request-failed', error: failureFromUnknown(cause) })
    })
    void runtime.subscribeDesktopEvents((event) => {
      if (!disposed) dispatch({ type: 'desktop-event', event })
    }).then((off) => {
      if (disposed) off()
      else unsubscribes.push(off)
    }).catch((cause) => {
      if (!disposed) dispatch({ type: 'request-failed', error: failureFromUnknown(cause) })
    })
    void runtime.subscribeAppUpdates((event) => {
      if (!disposed && (event.source === 'manual' || event.state.phase !== 'failed')) {
        if (event.source === 'manual') manualUpdateSeen.current = true
        setAppUpdate(event.state)
      }
    }).then((off) => {
      if (disposed) off()
      else unsubscribes.push(off)
    }).catch(() => { /* 自动更新监听失败不阻塞工作台。 */ })
    void runtime.subscribeLocalAppEvents((event) => {
      if (!disposed) setActiveApp((current) => applyLocalAppEvent(current, event))
    }).then((off) => {
      if (disposed) off()
      else unsubscribes.push(off)
    }).catch(() => { /* 本地应用事件监听失败不阻塞工作台。 */ })
    void Promise.all([runtime.migrationStatus(), runtime.recoveryStatus()]).then(([status, recoveryStatus]) => {
      if (disposed) return
      setMigration(status)
      setRecovery(recoveryStatus)
      if (status.phase === 'ready' && recoveryStatus === null) void start(false)
    }).catch((cause) => {
      if (!disposed) dispatch({ type: 'request-failed', error: failureFromUnknown(cause) })
    })
    return () => { disposed = true; unsubscribes.forEach((off) => off()) }
  }, [runtime, start])

  useEffect(() => {
    let subscribed = true
    if (startupUpdate.current?.runtime !== runtime) {
      manualUpdateSeen.current = false
      startupUpdate.current = {
        runtime,
        receipt: runtime.takeAppUpdateReceipt(),
        check: runtime.checkAppUpdate('automatic'),
      }
    }
    const startup = startupUpdate.current
    void startup.receipt
      .then((receipt) => { if (subscribed) setAppUpdateReceipt(receipt) })
      .catch(() => { /* 回执读取失败只进入诊断，不打扰启动。 */ })
    void startup.check
      .then((next) => { if (subscribed && !manualUpdateSeen.current) setAppUpdate(next) })
      .catch(() => { /* 自动检查失败保持静默，用户可从原生菜单手动重试。 */ })
    return () => { subscribed = false }
  }, [runtime])

  const deferMigration = async () => {
    await runtime.deferMigration()
    setMigration({ phase: 'deferred' })
  }

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      const nextTheme = themeFromWorkbenchMessage(event, iframeRef.current?.contentWindow ?? null)
      if (nextTheme) setShellTheme((previous) => previous === nextTheme ? previous : nextTheme)
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [])

  useEffect(() => {
    // Runtime 离开就绪态（失败/重启）后，本地应用视图不再有效；避免陈旧状态跨 generation 存留。
    if (state.rendererUrl === null) setActiveApp(null)
  }, [state.rendererUrl])

  useEffect(() => {
    if (state.generationId === null || state.rendererUrl === null) return
    const active = {
      generationId: state.generationId,
      origin: new URL(state.rendererUrl).origin,
    }
    const bridge = createWorkbenchBridge({
      frame: () => iframeRef.current,
      active: () => active,
      invoke: (command, args) => invoke(command, args),
    })
    const onMessage = (event: MessageEvent) => { void bridge.onMessage(event) }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [state.generationId, state.rendererUrl])

  const percent = useMemo(() => {
    const { progress } = state
    if (progress === null || progress.total === null || progress.total <= 0) return null
    return Math.min(100, Math.round(progress.completed / progress.total * 100))
  }, [state.progress])

  const exportDiagnostics = async () => {
    try {
      const path = await runtime.exportDiagnostics()
      dispatch({ type: 'diagnostics-exported', path })
    } catch (cause) {
      dispatch({ type: 'request-failed', error: failureFromUnknown(cause) })
    }
  }

  const stopActiveApp = async () => {
    if (activeApp === null || state.generationId === null) return
    const workspaceId = activeApp.workspaceId
    setActiveApp(null)
    try {
      await invoke('app_stop', { workspaceId, generationId: state.generationId })
    } catch { /* 停止失败由 Rust 侧记录；界面先返回工作台。 */ }
  }

  const runUpdate = async (action: () => Promise<AppUpdateState>) => {
    try {
      setUpdateDiagnosticPath(null)
      setAppUpdate(await action())
      return true
    } catch (cause) {
      setAppUpdate({ phase: 'failed', update: updateFailureFromUnknown(cause) })
      return false
    }
  }

  const installUpdateNow = async () => {
    try {
      await runtime.installAppUpdateNow()
    } catch (cause) {
      setAppUpdate({ phase: 'failed', update: updateFailureFromUnknown(cause) })
    }
  }

  const scheduleUpdateOnExit = async () => {
    if (await runUpdate(runtime.installAppUpdateOnExit)) setInstallOnExit(true)
  }

  const deferAppUpdate = async () => {
    if (appUpdate.phase === 'failed') {
      setAppUpdate({ phase: 'idle' })
      return
    }
    await runUpdate(runtime.deferAppUpdate)
    setInstallOnExit(false)
  }

  const openManualUpdate = async () => {
    try {
      await runtime.openAppUpdateDownload()
    } catch (cause) {
      setAppUpdate({ phase: 'failed', update: updateFailureFromUnknown(cause) })
    }
  }

  const exportUpdateDiagnostics = async () => {
    try {
      setUpdateDiagnosticPath(await runtime.exportDiagnostics())
    } catch (cause) {
      setAppUpdate({ phase: 'failed', update: updateFailureFromUnknown(cause) })
    }
  }

  const failed = state.phase === 'failed'
  const failureText = state.error === null
    ? null
    : state.error.message === backupEvidenceLostMessage
      ? backupEvidenceLostMessage
      : failureCopy[state.error.code] ?? failureCopy.internal
  // 只为「技术信息」行保留最近一次失败的原始消息;主文案由 failureCopy 提供。
  const [lastFailure, setLastFailure] = useState<RuntimeFailure | null>(state.error)

  useEffect(() => {
    if (state.error !== null) setLastFailure(state.error)
  }, [state.error])

  return (
    <main className="windowShell" data-theme={shellTheme}>
      <TitleBar controls={windowControls} />
      <div className="windowContent">
        <AppUpdateBanner
            state={appUpdate}
            receipt={appUpdateReceipt}
            installOnExit={installOnExit}
            diagnosticPath={updateDiagnosticPath}
            onDownload={() => void runUpdate(runtime.downloadAppUpdate)}
            onInstallNow={() => void installUpdateNow()}
            onInstallOnExit={() => void scheduleUpdateOnExit()}
            onDefer={() => void deferAppUpdate()}
            onOpenManual={() => void openManualUpdate()}
            onRetry={() => void runUpdate(() => runtime.checkAppUpdate('manual'))}
            onDismissReceipt={() => setAppUpdateReceipt(null)}
            onExportDiagnostics={() => void exportUpdateDiagnostics()}
          />
        {state.rendererUrl !== null ? (
          <>
            {/* 受管工作台与桌面壳不同源；不委托 clipboard-write 时 WebView2 会拦截官方 UI 的复制操作。 */}
            <iframe
              ref={iframeRef}
              className="workbenchFrame"
              title="DeepSeek Harness 工作台"
              src={workbenchUrl(state.rendererUrl)}
              data-hidden={activeApp !== null || undefined}
              allow="clipboard-write"
            />
            {activeApp !== null && (
              <section className="localAppSurface" aria-label="本地应用视图">
                <div className="localAppStrip">
                  <span className="localAppStripTitle">正在运行：{activeApp.title}</span>
                  <div className="localAppStripActions">
                    <button type="button" onClick={() => setActiveApp(null)}>返回工作台</button>
                    <button type="button" className="localAppStripStop" onClick={() => void stopActiveApp()}>停止应用</button>
                  </div>
                </div>
                <iframe
                  className="localAppFrame"
                  title={`本地应用 ${activeApp.title}`}
                  src={activeApp.origin}
                  allow="clipboard-write"
                />
              </section>
            )}
          </>
        ) : (
          <div className="bootstrapShell">
            <section className="bootstrapCard" aria-live="polite" data-failed={failed || undefined}>
        {recovery !== null ? (
          <section className="migrationPanel" aria-live="polite">
            <p className="eyebrow">DEEPSEEK HARNESS DESKTOP · 安全恢复</p>
            <h1>Agent 数据库需要人工恢复</h1>
            <p className="statusMessage">已阻止 Runtime 和所有写操作。请保留源库，并使用下列已重新验证的备份证据进行人工恢复。</p>
            <dl className="migrationSummary">
              <div><dt>源库</dt><dd>{recovery.source}</dd></div>
              <div><dt>备份</dt><dd>{recovery.backup}</dd></div>
              <div><dt>SHA-256</dt><dd>{recovery.sha256}</dd></div>
              <div><dt>长度</dt><dd>{recovery.length} bytes</dd></div>
              <div><dt>Schema</dt><dd>{recovery.schema}</dd></div>
              <div><dt>Sidecar</dt><dd>{recovery.sidecar}</dd></div>
            </dl>
          </section>
        ) : migration?.phase === 'deferred' ? (
          <section className="migrationPanel" aria-live="polite">
            <p className="eyebrow">DEEPSEEK HARNESS DESKTOP · 数据迁移</p>
            <h1>数据迁移已暂缓</h1>
            <p className="statusMessage">应用仍处于阻断状态；重新启动后可继续确认迁移。</p>
          </section>
        ) : migration !== null && migration.phase !== 'ready' ? (
          <MigrationPrompt
            migration={migration}
            onConfirm={() => runtime.confirmMigration()}
            onDefer={deferMigration}
          />
        ) : (
          <>
        <header className="brandRow">
          <span className="brandVisual" aria-hidden="true">
            <span className="orbitLayer orbitLayerOuter"><span className="orbitParticle" /></span>
            <span className="orbitLayer orbitLayerInner"><span className="orbitParticle" /></span>
            <DeepSeekFishLogo className="brandFish" size={31} />
          </span>
          <span className="brandName">deepseek</span>
          <span className="brandTag">HARNESS</span>
        </header>

        <div className="statusBlock">
          <p className="eyebrow">DEEPSEEK HARNESS DESKTOP · {phaseLabels[state.phase]}</p>
          <h1>{failed ? '启动遇到问题' : '准备你的 DeepSeek Harness'}</h1>
          <p className={failed ? 'statusMessage errorText' : 'statusMessage'}>{failed && failureText !== null ? failureText : state.message}</p>
          {state.versionTransition !== null && <p className="versionTransition">{state.versionTransition}</p>}
          {state.recoveryNotice !== null && <p className="recoveryNotice">{state.recoveryNotice}</p>}
        </div>

        {!failed && (
          <div className="progressGroup">
            <div className="progressTrack" role="progressbar" aria-label="运行时准备进度" aria-valuenow={percent ?? undefined}>
              <span className={percent === null ? 'progressFill indeterminate' : 'progressFill'} style={percent === null ? undefined : { width: `${percent}%` }} />
            </div>
            <div className="progressMeta">
              <span>{percent === null ? '请稍候…' : `${percent}%`}</span>
              {state.phase === 'downloading' && <button className="textButton" onClick={() => void runtime.cancelRuntime()}>取消</button>}
            </div>
          </div>
        )}

        {failed && (
          <div className="actionRow">
            {state.error?.recoverable !== false && <button className="primaryButton" onClick={() => void start(false)}>重试</button>}
            <button className="secondaryButton" onClick={() => void start(true)}>修复 DeepSeek Harness</button>
            <button className="secondaryButton" onClick={() => void exportDiagnostics()}>导出诊断</button>
          </div>
        )}

        {state.diagnosticPath !== null && <p className="diagnosticPath">诊断已保存：{state.diagnosticPath}</p>}

        <button className="detailsToggle" aria-expanded={detailsOpen} onClick={() => setDetailsOpen((open) => !open)}>
          {detailsOpen ? '收起详情' : '查看详情'}
        </button>
        {detailsOpen && (
          <dl className="detailsPanel">
            <div><dt>阶段</dt><dd>{phaseLabels[state.phase]}</dd></div>
            <div><dt>操作</dt><dd>{state.operationId ?? '等待分配'}</dd></div>
            <div><dt>平台</dt><dd>Windows x64 / macOS Apple Silicon</dd></div>
            {lastFailure !== null && <div><dt>技术信息</dt><dd>{lastFailure.message}</dd></div>}
          </dl>
        )}
          </>
        )}
            </section>
          </div>
        )}
      </div>
    </main>
  )
}

function isValidLoopbackOrigin(origin: string): boolean {
  try {
    const url = new URL(origin)
    return url.protocol === 'http:'
      && url.hostname === '127.0.0.1'
      && url.port !== ''
      && url.port.length <= 5
      && Number(url.port) > 0
      && Number(url.port) <= 65535
  } catch {
    return false
  }
}

function applyLocalAppEvent(
  current: { workspaceId: string; origin: string; title: string } | null,
  event: LocalAppEvent,
) {
  if (event.origin !== null && !isValidLoopbackOrigin(event.origin)) return current
  if (event.kind === 'launched' && event.origin !== null) {
    return { workspaceId: event.workspaceId, origin: event.origin, title: event.title ?? event.workspaceId }
  }
  if (current !== null && current.workspaceId === event.workspaceId) return null
  return current
}
