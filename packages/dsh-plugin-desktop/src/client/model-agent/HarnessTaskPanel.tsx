import { useCallback, useEffect, useMemo, useState } from 'react'
import type { DesktopBridgeLike } from '../desktop-bridge'
import { messageOf } from './state'

interface HarnessStatus {
  state: 'idle' | 'running' | 'completed' | 'blocked' | 'failed' | 'cancelled' | string
  pid?: number
  requestId?: string
  errorCode?: string
}

interface HarnessTaskForm {
  taskContractPath: string
  understandingPath: string
  worktreeRoot: string
  knowledgeHome: string
  authorizationId: string
  agentBackend: string
}

const initialForm: HarnessTaskForm = {
  taskContractPath: '',
  understandingPath: '',
  worktreeRoot: '',
  knowledgeHome: '',
  authorizationId: '',
  agentBackend: 'host-bridge',
}

export function HarnessTaskPanel({ bridge }: { bridge: DesktopBridgeLike }) {
  const [form, setForm] = useState<HarnessTaskForm>(initialForm)
  const [status, setStatus] = useState<HarnessStatus>({ state: 'idle' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setStatus(await bridge.requestV2<HarnessStatus>('harness.status'))
    } catch (cause) {
      setError(messageOf(cause))
    }
  }, [bridge])

  useEffect(() => { void refresh() }, [refresh])

  const missing = useMemo(() => Object.entries(form)
    .filter(([key, value]) => key !== 'agentBackend' && value.trim() === '')
    .map(([key]) => key), [form])
  const running = status.state === 'running'

  useEffect(() => {
    if (!running) return
    const timer = window.setInterval(() => { void refresh() }, 1500)
    return () => window.clearInterval(timer)
  }, [refresh, running])

  const start = async () => {
    if (missing.length > 0 || busy) return
    setBusy(true)
    setError(null)
    try {
      setStatus(await bridge.requestV2<HarnessStatus>('harness.start', undefined, {
        taskContractPath: form.taskContractPath.trim(),
        understandingPath: form.understandingPath.trim(),
        worktreeRoot: form.worktreeRoot.trim(),
        knowledgeHome: form.knowledgeHome.trim(),
        authorizationId: form.authorizationId.trim(),
        ...(form.agentBackend.trim() === '' ? {} : { agentBackend: form.agentBackend.trim() }),
      }))
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(false)
    }
  }

  const cancel = async () => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      setStatus(await bridge.requestV2<HarnessStatus>('harness.cancel'))
    } catch (cause) {
      setError(messageOf(cause))
    } finally {
      setBusy(false)
    }
  }

  const update = (key: keyof HarnessTaskForm, value: string) => setForm((current) => ({ ...current, [key]: value }))
  return (
    <div className="dshModelAgentGrid">
      <article className="dshModelAgentCard dshHarnessTaskPanel">
        <div className="dshModelAgentCardHeader">
          <div><div className="dshModelAgentEyebrow">HARNESS GOVERNED EXECUTION</div><h3>Harness 任务执行</h3></div>
          <span className={`dshModelAgentStatus dshModelAgentStatus-${status.state}`}>{statusLabel(status.state)}</span>
        </div>
        <p className="dshModelAgentCardText">Harness 负责需求背景、场景、目标、项目和调用链的决策；模型只执行已确认方案，不自行改方案。</p>
        <div className="dshModelAgentWarning" role="note">启动前必须使用 Harness 已生成的任务契约和需求理解文件。缺任一证据，Harness 会拒绝进入修改阶段。</div>
        <label className="dshModelAgentField">任务契约文件绝对路径<input aria-label="任务契约文件绝对路径" value={form.taskContractPath} onChange={(event) => update('taskContractPath', event.target.value)} placeholder="/path/to/task-contract.json" /></label>
        <label className="dshModelAgentField">需求理解文件绝对路径<input aria-label="需求理解文件绝对路径" value={form.understandingPath} onChange={(event) => update('understandingPath', event.target.value)} placeholder="/path/to/understanding.json" /></label>
        <label className="dshModelAgentField">目标项目绝对路径<input aria-label="目标项目绝对路径" value={form.worktreeRoot} onChange={(event) => update('worktreeRoot', event.target.value)} placeholder="/path/to/project" /></label>
        <label className="dshModelAgentField">知识库目录绝对路径<input aria-label="知识库目录绝对路径" value={form.knowledgeHome} onChange={(event) => update('knowledgeHome', event.target.value)} placeholder="/path/to/knowledge" /></label>
        <label className="dshModelAgentField">执行授权编号<input aria-label="执行授权编号" value={form.authorizationId} onChange={(event) => update('authorizationId', event.target.value)} placeholder="DFHIS-32178-change-1" /></label>
        <label className="dshModelAgentField">模型执行后端（可选）<input aria-label="模型执行后端" value={form.agentBackend} onChange={(event) => update('agentBackend', event.target.value)} placeholder="host-bridge" /></label>
        {missing.length > 0 && <p className="dshModelAgentMuted" role="status">还缺少 {missing.length} 项启动参数；这些参数应由 Harness 需求归档/理解阶段生成。</p>}
        {error !== null && <div className="dshModelAgentError" role="alert">{error}</div>}
        <div className="dshModelAgentActions">
          <button type="button" disabled={busy || running || missing.length > 0} onClick={() => void start()}>按 Harness 决策执行</button>
          <button type="button" disabled={busy || !running} onClick={() => void cancel()}>取消 Harness 任务</button>
          <button type="button" disabled={busy} onClick={() => void refresh()}>刷新状态</button>
        </div>
        {status.errorCode !== undefined && <p className="dshModelAgentCardHint">错误码：{status.errorCode}</p>}
      </article>
    </div>
  )
}

function statusLabel(value: string): string {
  return ({ idle: '待启动', running: '执行中', completed: '已完成', blocked: '已阻断', failed: '失败', cancelled: '已取消' } as Record<string, string>)[value] ?? value
}
