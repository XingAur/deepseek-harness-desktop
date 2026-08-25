import { useCallback, useEffect, useMemo, useState } from 'react'
import { createAgentEventBridge } from './agent-event-bridge'
import type { AgentEventEnvelope } from './agent-events'
import type { DesktopBridgeLike } from './desktop-bridge'

type PermissionMode = 'request-approval' | 'smart-approval' | 'full-access'
type TaskStatus = 'active' | 'running' | 'paused' | 'waiting-approval' | 'needs-review' | 'completed' | 'failed' | 'cancelled'

interface TaskSummary {
  taskId: string
  workerSessionId: string
  generationId: string
  providerId: string
  agentId: string
  workspaceId: string
  permission: PermissionMode
  status: TaskStatus | string
}

interface ApprovalSummary {
  approvalId: string
  taskId: string
  capabilityKind: string
  scope: string
  status: string
}

interface AgentWorkbenchProps {
  bridge: DesktopBridgeLike
  workspaceId?: string
  providerOptions?: Array<{ id: string; label: string }>
}

const defaultProviders = [
  { id: 'codex', label: 'Codex' },
  { id: 'claude', label: 'Claude' },
]

export function AgentWorkbench({ bridge, workspaceId = '', providerOptions = defaultProviders }: AgentWorkbenchProps) {
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const [approvals, setApprovals] = useState<ApprovalSummary[]>([])
  const [events, setEvents] = useState<AgentEventEnvelope[]>([])
  const [prompt, setPrompt] = useState('')
  const [providerId, setProviderId] = useState(providerOptions[0]?.id ?? 'codex')
  const [permission, setPermission] = useState<PermissionMode>('request-approval')
  const [diff, setDiff] = useState<string | null>(null)
  const [gap, setGap] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [recoveredTaskIds, setRecoveredTaskIds] = useState<Set<string>>(new Set())
  const selectedTask = tasks.find((task) => task.taskId === selectedTaskId) ?? null

  const loadTasks = useCallback(async () => {
    try {
      setTasks(await bridge.requestV2<TaskSummary[]>('task.list', undefined, { workspaceId }))
      setError(null)
    } catch (cause) {
      setError(messageOf(cause))
    }
  }, [bridge, workspaceId])

  const loadApprovals = useCallback(async (taskId: string) => {
    try {
      setApprovals(await bridge.requestV2<ApprovalSummary[]>('approval.list', undefined, { taskId }))
      setError(null)
    } catch (cause) {
      setApprovals([])
      setError(messageOf(cause))
    }
  }, [bridge])

  useEffect(() => { void loadTasks() }, [loadTasks])

  useEffect(() => {
    const targetOrigin = parentOrigin()
    const eventBridge = createAgentEventBridge({
      parent: () => window.parent,
      targetOrigin,
      onEvent(event) {
        setEvents((current) => [...current.slice(-99), event])
        if (event.type === 'approval.requested') {
          setSelectedTaskId((current) => current ?? event.taskId)
          void loadApprovals(event.taskId)
        }
        if (event.type === 'task.completed' || event.type === 'task.failed' || event.type === 'task.cancelled') void loadTasks()
      },
      onReplayRequest() { setGap(true); void loadTasks() },
    })
    const onMessage = (message: MessageEvent) => eventBridge.onMessage(message)
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [loadApprovals, loadTasks])

  const createTask = async () => {
    if (workspaceId.trim() === '' || prompt.trim() === '') return
    try {
      const task = await bridge.requestV2<TaskSummary>('task.create', undefined, {
        workspaceId,
        prompt: prompt.trim(),
        permission,
        providerId,
        agentId: `${providerId}:default`,
      })
      setTasks((current) => [task, ...current.filter((item) => item.taskId !== task.taskId)])
      setSelectedTaskId(task.taskId)
      setPrompt('')
      setError(null)
    } catch (cause) {
      setError(messageOf(cause))
    }
  }

  const changeTask = async (action: 'task.start' | 'task.cancel' | 'task.resume' | 'task.recover', task: TaskSummary) => {
    try {
      const next = await bridge.requestV2<TaskSummary>(action, undefined, action === 'task.recover'
        ? { taskId: task.taskId, workspaceId: task.workspaceId, sourceSessionId: task.workerSessionId }
        : { taskId: task.taskId })
      setTasks((current) => current.map((item) => item.taskId === task.taskId ? next : item))
      if (action === 'task.recover') {
        setRecoveredTaskIds((current) => new Set(current).add(task.taskId))
        await loadApprovals(task.taskId)
      }
      if (action === 'task.cancel') setApprovals([])
    } catch (cause) {
      setError(messageOf(cause))
    }
  }

  const resolveApproval = async (approval: ApprovalSummary, decision: 'allow-once' | 'allow-for-task' | 'deny') => {
    if (selectedTask === null) return
    try {
      await bridge.requestV2('approval.resolve', undefined, { approvalId: approval.approvalId, taskId: selectedTask.taskId, decision })
      await loadApprovals(selectedTask.taskId)
    } catch (cause) {
      setError(messageOf(cause))
    }
  }

  const readDiff = async (contentRefId: string) => {
    if (selectedTask === null) return
    try {
      const result = await bridge.requestV2<{ content: string }>('content-reference.read', undefined, { contentRefId, taskId: selectedTask.taskId, offset: 0, length: 16 * 1024 })
      setDiff(result.content)
    } catch (cause) {
      setError(messageOf(cause))
    }
  }

  const selectedEvents = useMemo(() => selectedTask === null ? [] : events.filter((event) => event.taskId === selectedTask.taskId).slice(-50), [events, selectedTask])

  return (
    <section className="dshAgentWorkbench" aria-label="Agent 工作台">
      <header className="dshAgentWorkbenchHeader">
        <div><p className="dshModelAgentEyebrow">AGENT WORKBENCH</p><h3>Agent 工作台</h3><p>任务、审批、进度和文件变更都在同一条可恢复会话中。</p></div>
        <button type="button" onClick={() => void loadTasks()}>刷新任务</button>
      </header>
      {providerId === 'codex'
        ? <div className="dshAgentWorkbenchNotice" role="note">Codex 通过本机官方 CLI 真实执行。写入文件、运行命令等敏感操作会先弹出审批，由你决定是否放行。</div>
        : <div className="dshAgentWorkbenchWarning" role="note">这个 Provider 暂时只有协议预览实现，不会执行真实模型操作。</div>}
      <div className="dshAgentWorkbenchCreate">
        <label>Provider<select aria-label="Provider" value={providerId} onChange={(event) => setProviderId(event.target.value)}>{providerOptions.map((provider) => <option key={provider.id} value={provider.id}>{provider.label}</option>)}</select></label>
        <label>权限模式<select aria-label="权限模式" value={permission} onChange={(event) => setPermission(event.target.value as PermissionMode)}><option value="request-approval">请求批准</option><option value="smart-approval">智能批准</option><option value="full-access">完全访问权限</option></select></label>
        <label className="dshAgentWorkbenchPrompt">任务提示<textarea aria-label="任务提示" value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="告诉 Agent 需要完成什么" /></label>
        <button type="button" className="dshAgentWorkbenchPrimary" disabled={workspaceId.trim() === '' || prompt.trim() === ''} onClick={() => void createTask()}>创建任务</button>
      </div>
      {permission === 'full-access' && <div className="dshAgentWorkbenchWarning" role="alert">完全访问权限会允许 Agent 在当前工作区执行更多操作。每个任务仍受宿主边界、路径校验和审计约束。</div>}
      {workspaceId.trim() === '' && <p className="dshModelAgentMuted">当前没有可用工作区，先在左侧选择或创建项目。</p>}
      {gap && <div className="dshAgentWorkbenchWarning" role="status">检测到事件序号间隔，已重新载入任务状态；未确认的审批请再次检查。</div>}
      {error !== null && <div className="dshAgentWorkbenchError" role="alert">{error}</div>}
      <div className="dshAgentWorkbenchColumns">
        <div className="dshAgentWorkbenchTaskList"><h4>任务</h4>{tasks.length === 0 ? <p className="dshModelAgentMuted">暂无任务。</p> : tasks.map((task) => <button type="button" aria-label={`任务 ${task.taskId}`} className={`dshAgentWorkbenchTask${task.taskId === selectedTaskId ? ' selected' : ''}`} key={task.taskId} onClick={() => { setSelectedTaskId(task.taskId); void loadApprovals(task.taskId) }}><strong>{task.taskId}</strong><span>{task.providerId} · {statusLabel(task.status)}</span><small>{task.permission}</small></button>)}</div>
        <div className="dshAgentWorkbenchDetail"><h4>{selectedTask === null ? '选择一个任务' : `任务 ${selectedTask.taskId}`}</h4>{selectedTask !== null && <><div className="dshAgentWorkbenchActions">{selectedTask.status === 'active' || selectedTask.status === 'paused' ? <button type="button" onClick={() => void changeTask(selectedTask.status === 'paused' ? 'task.resume' : 'task.start', selectedTask)}>{selectedTask.status === 'paused' ? '恢复任务' : '开始任务'}</button> : null}{selectedTask.status !== 'cancelled' && selectedTask.status !== 'completed' && selectedTask.status !== 'needs-review' && <button type="button" onClick={() => void changeTask('task.cancel', selectedTask)}>取消任务</button>}</div>{selectedTask.status === 'needs-review' && <div className="dshAgentWorkbenchWarning" role="status">这个任务的外部执行结果无法确认，已暂停自动恢复。请检查工作区和外部服务后，再创建新任务；系统不会重复执行未知操作。</div>}{selectedTask.status === 'waiting-approval' && <div className="dshAgentWorkbenchWarning" role="status">任务正在等待审批。审批通过前不会继续执行。</div>}<section><h5>待处理审批</h5>{approvals.length === 0 ? <p className="dshModelAgentMuted">暂无待处理审批。</p> : approvals.map((approval) => <div className="dshAgentWorkbenchApproval" key={approval.approvalId}><div><strong>{approval.capabilityKind}</strong><span>{approval.scope}</span></div><div><button type="button" onClick={() => void resolveApproval(approval, 'allow-once')}>允许一次</button><button type="button" onClick={() => void resolveApproval(approval, 'allow-for-task')}>允许本任务</button><button type="button" onClick={() => void resolveApproval(approval, 'deny')}>拒绝</button></div></div>)}</section><section><h5>事件时间线</h5>{selectedEvents.length === 0 ? <p className="dshModelAgentMuted">等待 Agent 事件。</p> : <ol className="dshAgentWorkbenchTimeline">{selectedEvents.map((event) => <li key={`${event.taskId}:${event.sequence}`}><span>{event.type}</span>{event.type === 'message.delta' && isRecord(event.payload) && typeof event.payload.text === 'string' ? <p>{event.payload.text}</p> : event.type === 'file.diff.available' && contentRefId(event.payload) ? <button type="button" onClick={() => void readDiff(contentRefId(event.payload) as string)}>查看变更</button> : null}</li>)}</ol>}</section></>}</div>
      </div>
      {selectedTask !== null && (selectedTask.status === 'waiting-approval' || selectedTask.status === 'needs-review') && <button type="button" onClick={() => void changeTask('task.recover', selectedTask)}>接管待复核任务</button>}
      {selectedTask !== null && selectedTask.status === 'needs-review' && recoveredTaskIds.has(selectedTask.taskId) && <button type="button" onClick={() => void changeTask('task.cancel', selectedTask)}>取消待复核任务</button>}
      {diff !== null && <section className="dshAgentWorkbenchDiff"><div><h4>文件变更</h4><button type="button" onClick={() => setDiff(null)}>关闭</button></div><pre>{diff}</pre></section>}
    </section>
  )
}

function parentOrigin(): string {
  const referrer = document.referrer
  try { return referrer === '' ? window.location.origin : new URL(referrer).origin } catch { return window.location.origin }
}

function statusLabel(status: string): string {
  return {
    active: '待开始',
    running: '运行中',
    paused: '已暂停',
    'waiting-approval': '等待审批',
    'needs-review': '待复核',
    completed: '已完成',
    failed: '失败',
    cancelled: '已取消',
  }[status] ?? status
}

function contentRefId(value: unknown): string | null {
  if (!isRecord(value) || !isRecord(value.contentRef) || typeof value.contentRef.id !== 'string') return null
  return value.contentRef.id
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function messageOf(cause: unknown): string {
  return cause instanceof Error && cause.message.length > 0 ? cause.message : 'Agent 请求未能完成'
}
