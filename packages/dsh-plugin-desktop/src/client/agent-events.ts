export const AGENT_EVENT_CHANNEL = 'dsh-agent/v1' as const

export type AgentEventType =
  | 'task.created'
  | 'task.started'
  | 'task.progress'
  | 'task.waiting-approval'
  | 'task.completed'
  | 'task.failed'
  | 'task.cancelled'
  | 'worker.interrupted'
  | 'worker.recoverable'
  | 'approval.requested'
  | 'approval.resolved'
  | 'message.delta'
  | 'message.completed'
  | 'tool.started'
  | 'tool.output'
  | 'tool.completed'
  | 'command.started'
  | 'command.output'
  | 'command.completed'
  | 'file.changed'
  | 'file.diff.available'
  | 'usage.updated'
  | 'extension.called'

export interface AgentEventEnvelope {
  channel: typeof AGENT_EVENT_CHANNEL
  generationId: string
  taskId: string
  sessionId: string
  sequence: number
  type: AgentEventType
  payload: unknown
}

const eventTypes = new Set<AgentEventType>([
  'task.created', 'task.started', 'task.progress', 'task.waiting-approval', 'task.completed',
  'task.failed', 'task.cancelled', 'worker.interrupted', 'worker.recoverable',
  'approval.requested', 'approval.resolved', 'message.delta', 'message.completed',
  'tool.started', 'tool.output', 'tool.completed', 'command.started', 'command.output',
  'command.completed', 'file.changed', 'file.diff.available', 'usage.updated', 'extension.called',
])

export function isAgentEventEnvelope(value: unknown): value is AgentEventEnvelope {
  if (!isRecord(value)) return false
  if (Object.keys(value).sort().join(',') !== 'channel,generationId,payload,sequence,sessionId,taskId,type') return false
  return value.channel === AGENT_EVENT_CHANNEL
    && validId(value.generationId)
    && validId(value.taskId)
    && validId(value.sessionId)
    && Number.isSafeInteger(value.sequence)
    && (value.sequence as number) >= 1
    && typeof value.type === 'string'
    && eventTypes.has(value.type as AgentEventType)
    && isRecord(value.payload)
    && !containsSecretShape(value.payload)
    && byteSize(value) <= 32 * 1024
}

export type AgentEventCheckpointResult =
  | { status: 'accepted'; event: AgentEventEnvelope }
  | { status: 'duplicate' }
  | { status: 'gap'; expectedSequence: number }

export function createAgentEventCheckpoint() {
  const lastSequence = new Map<string, number>()
  return {
    accept(event: unknown): AgentEventCheckpointResult {
      if (!isAgentEventEnvelope(event)) return { status: 'duplicate' }
      const key = event.generationId + ':' + event.taskId + ':' + event.sessionId
      const previous = lastSequence.get(key) ?? 0
      if (event.sequence <= previous) return { status: 'duplicate' }
      if (event.sequence !== previous + 1) return { status: 'gap', expectedSequence: previous + 1 }
      lastSequence.set(key, event.sequence)
      return { status: 'accepted', event }
    },
  }
}

function containsSecretShape(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(containsSecretShape)
  if (!isRecord(value)) return false
  return Object.entries(value).some(([key, nested]) => {
    if (/^(api[_-]?key|access[_-]?token|refresh[_-]?token|token|oauth|authorization|cookie|set-cookie|secret|password|private[_-]?key)$/i.test(key)) return true
    return containsSecretShape(nested)
  })
}

function validId(value: unknown): value is string {
  return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function byteSize(value: unknown): number {
  try {
    return new TextEncoder().encode(JSON.stringify(value)).byteLength
  } catch {
    return Number.POSITIVE_INFINITY
  }
}
