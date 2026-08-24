import { DESKTOP_BRIDGE_MAX_BYTES, containsSecretShape, isRecord, validRequestId } from './bridge-contract'

export const AGENT_EVENT_CHANNEL = 'dsh-agent/v1' as const
export const AGENT_TAURI_EVENT_NAME = 'agent-event' as const

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
  'task.created',
  'task.started',
  'task.progress',
  'task.waiting-approval',
  'task.completed',
  'task.failed',
  'task.cancelled',
  'worker.interrupted',
  'worker.recoverable',
  'approval.requested',
  'approval.resolved',
  'message.delta',
  'message.completed',
  'tool.started',
  'tool.output',
  'tool.completed',
  'command.started',
  'command.output',
  'command.completed',
  'file.changed',
  'file.diff.available',
  'usage.updated',
  'extension.called',
])

export function isAgentEventEnvelope(value: unknown): value is AgentEventEnvelope {
  if (!isRecord(value)) return false
  const keys = Object.keys(value).sort()
  if (keys.join(',') !== 'channel,generationId,payload,sequence,sessionId,taskId,type') return false
  return value.channel === AGENT_EVENT_CHANNEL
    && validRequestId(value.generationId)
    && validRequestId(value.taskId)
    && validRequestId(value.sessionId)
    && Number.isSafeInteger(value.sequence)
    && (value.sequence as number) >= 1
    && typeof value.type === 'string'
    && eventTypes.has(value.type as AgentEventType)
    && isRecord(value.payload)
    && !containsSecretShape(value.payload)
    && boundedJson(value)
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

function boundedJson(value: unknown): boolean {
  try {
    return new TextEncoder().encode(JSON.stringify(value)).byteLength <= DESKTOP_BRIDGE_MAX_BYTES
  } catch {
    return false
  }
}
