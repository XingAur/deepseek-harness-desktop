import { useCallback, useEffect, useMemo, useReducer, useState } from 'react'
import type { RuntimeClient, RuntimePhase } from './runtime-contract'
import { failureFromUnknown, initialRuntimeState, runtimeReducer } from './runtime-reducer'
import { TitleBar } from './TitleBar'
import type { WindowControls } from './window-client'

const phaseLabels: Record<RuntimePhase, string> = {
  checking: '检查运行环境',
  'fetching-manifest': '获取运行时清单',
  downloading: '下载运行时',
  verifying: '验证文件完整性',
  activating: '激活运行时',
  starting: '启动 DeepSeek Harness',
  ready: '准备完成',
  cancelled: '操作已取消',
  failed: '需要处理',
}

export interface AppProps {
  runtime: RuntimeClient
  windowControls: WindowControls
}

export function App({ runtime, windowControls }: AppProps) {
  const [state, dispatch] = useReducer(runtimeReducer, initialRuntimeState)
  const [detailsOpen, setDetailsOpen] = useState(false)

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
    let unsubscribe: (() => void) | undefined
    void runtime.subscribeRuntimeProgress((event) => {
      if (!disposed) dispatch({ type: 'runtime-event', event })
    }).then((off) => {
      if (disposed) off()
      else unsubscribe = off
    }).catch((cause) => {
      if (!disposed) dispatch({ type: 'request-failed', error: failureFromUnknown(cause) })
    })
    void start(false)
    return () => { disposed = true; unsubscribe?.() }
  }, [runtime, start])

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

  const failed = state.phase === 'failed'
  return (
    <main className="windowShell">
      <TitleBar controls={windowControls} />
      <div className="windowContent">
        {state.rendererUrl !== null ? (
          <iframe className="workbenchFrame" title="DeepSeek Harness 工作台" src={state.rendererUrl} />
        ) : (
          <div className="bootstrapShell">
            <section className="bootstrapCard" aria-live="polite">
        <header className="brandRow">
          <span className="whaleMark" aria-hidden="true">◒</span>
          <span className="brandName">deepseek</span>
          <span className="brandTag">HARNESS</span>
        </header>

        <div className="statusBlock">
          <p className="eyebrow">DEEPSEEK HARNESS DESKTOP · {phaseLabels[state.phase]}</p>
          <h1>{failed ? '启动遇到问题' : '正在准备你的工作台'}</h1>
          <p className={failed ? 'statusMessage errorText' : 'statusMessage'}>{state.message}</p>
        </div>

        {!failed && (
          <div className="progressGroup">
            <div className="progressTrack" role="progressbar" aria-label="运行时准备进度" aria-valuenow={percent ?? undefined}>
              <span className={percent === null ? 'progressFill indeterminate' : 'progressFill'} style={percent === null ? undefined : { width: `${percent}%` }} />
            </div>
            <div className="progressMeta">
              <span>{percent === null ? '处理中' : `${percent}%`}</span>
              {state.phase === 'downloading' && <button className="textButton" onClick={() => void runtime.cancelRuntime()}>取消</button>}
            </div>
          </div>
        )}

        {failed && (
          <div className="actionRow">
            {state.error?.recoverable !== false && <button className="primaryButton" onClick={() => void start(false)}>重试</button>}
            <button className="secondaryButton" onClick={() => void start(true)}>修复运行时</button>
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
          </dl>
        )}
            </section>
          </div>
        )}
      </div>
    </main>
  )
}
