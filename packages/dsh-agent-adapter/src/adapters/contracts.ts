import type { AdapterKind, AgentEventType, PermissionProfile } from '../protocol.js'

export interface AgentStartRequest {
  sessionId: string
  prompt: string
  permission: PermissionProfile
  model?: string
  resume?: boolean
  signal?: AbortSignal
}

export interface AdapterSemanticEvent {
  type: AgentEventType
  payload: Record<string, unknown>
}

export interface AgentAdapterSession {
  run(): AsyncGenerator<AdapterSemanticEvent>
  cancel(): Promise<void>
}

export interface AgentAdapter {
  readonly adapterKind: AdapterKind
  start(request: AgentStartRequest): Promise<AgentAdapterSession>
}

export class AdapterProtocolError extends Error {
  readonly name = 'AdapterProtocolError'
  constructor(readonly code: string, message: string) {
    super(message)
  }
}

export function asRecord(value: unknown, message = 'adapter event must be an object'): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new AdapterProtocolError('malformed-event', message)
  return value as Record<string, unknown>
}

export function event(type: AgentEventType, payload: Record<string, unknown> = {}): AdapterSemanticEvent {
  return { type, payload }
}

export function assertSafeText(value: unknown, field: string, maximum = 16 * 1024): string {
  if (typeof value !== 'string' || value.length > maximum) throw new AdapterProtocolError('malformed-event', `${field} is invalid`)
  return value
}

export function assertNotAborted(signal: AbortSignal | undefined): void {
  if (signal?.aborted) throw new AdapterProtocolError('cancelled', 'Agent session cancelled')
}

export async function toAsyncIterable<T>(value: AsyncIterable<T> | Promise<AsyncIterable<T>>): Promise<AsyncIterable<T>> {
  return await value
}
