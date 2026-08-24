import type { AdapterKind } from '../protocol.js'
import { AdapterProtocolError, asRecord, assertNotAborted, assertSafeText, event, type AgentAdapter, type AgentAdapterSession, type AgentStartRequest, type AdapterSemanticEvent } from './contracts.js'

export interface CodexSdkThreadLike {
  id: string
  events: AsyncIterable<unknown>
}

export interface CodexSdkClientLike {
  createThread(input: { model?: string }): Promise<CodexSdkThreadLike>
  resumeThread?(threadId: string, input: { model?: string; prompt: string }): Promise<CodexSdkThreadLike>
  cancelThread?(threadId: string): Promise<void>
}

export interface CodexSdkAdapterOptions {
  client: CodexSdkClientLike
  mapping: Map<string, string>
}

export function createCodexSdkAdapter(options: CodexSdkAdapterOptions): AgentAdapter {
  return {
    adapterKind: 'codex-sdk' as AdapterKind,
    async start(request): Promise<AgentAdapterSession> {
      const existingThreadId = options.mapping.get(request.sessionId)
      let thread: CodexSdkThreadLike
      if (request.resume === true && existingThreadId !== undefined) {
        if (options.client.resumeThread === undefined) throw new AdapterProtocolError('resume-unsupported', 'Codex SDK does not support resume')
        thread = await options.client.resumeThread(existingThreadId, { ...(request.model === undefined ? {} : { model: request.model }), prompt: request.prompt })
      } else {
        thread = await options.client.createThread(request.model === undefined ? {} : { model: request.model })
      }
      if (!isSafeId(thread.id)) throw new AdapterProtocolError('invalid-thread', 'Codex SDK returned an invalid thread id')
      options.mapping.set(request.sessionId, thread.id)
      const controller = new AbortController()
      request.signal?.addEventListener('abort', () => controller.abort(), { once: true })
      return {
        async *run() {
          assertNotAborted(controller.signal)
          yield event(request.resume === true ? 'session.resumed' : 'session.started')
          let completed = false
          for await (const raw of thread.events) {
            assertNotAborted(controller.signal)
            const mapped = mapCodexEvent(raw)
            if (mapped === null) continue
            if (mapped.type === 'session.completed' || mapped.type === 'session.failed') completed = true
            yield mapped
          }
          if (!completed) yield event('session.completed')
        },
        async cancel() {
          controller.abort()
          await options.client.cancelThread?.(thread.id)
        },
      }
    },
  }
}

function mapCodexEvent(raw: unknown): AdapterSemanticEvent | null {
  const value = asRecord(raw)
  const type = assertSafeText(value.type, 'event.type', 128)
  switch (type) {
    case 'thread.started': return event('session.started')
    case 'thread.resumed': return event('session.resumed')
    case 'message.delta': return event('message.delta', { text: assertSafeText(value.text, 'event.text') })
    case 'message.completed': return event('message.completed', { text: assertSafeText(value.text ?? '', 'event.text') })
    case 'approval.requested': return event('approval.requested', pickSafe(value, ['requestId', 'capability', 'summary']))
    case 'approval.resolved': return event('approval.resolved', pickSafe(value, ['requestId', 'approved']))
    case 'progress.updated': return event('progress.updated', pickSafe(value, ['percent', 'message']))
    case 'tool.started': return event('tool.started', pickSafe(value, ['tool', 'callId']))
    case 'tool.completed': return event('tool.completed', pickSafe(value, ['tool', 'callId']))
    case 'command.started': return event('command.started', pickSafe(value, ['commandId']))
    case 'command.completed': return event('command.completed', pickSafe(value, ['commandId', 'exitCode']))
    case 'file.changed': return event('file.changed', pickSafe(value, ['path']))
    case 'file.diff.available': return event('file.diff.available', { contentRef: asRecord(value.contentRef, 'event.contentRef') })
    case 'usage.updated': return event('usage.updated', pickSafe(value, ['inputTokens', 'outputTokens']))
    case 'turn.completed':
    case 'task.completed': return event('session.completed')
    case 'error': throw new AdapterProtocolError('provider-error', assertSafeText(value.message ?? 'Codex SDK failed', 'event.message'))
    default: throw new AdapterProtocolError('unsupported-event', `Codex SDK event is not supported: ${type}`)
  }
}

function pickSafe(value: Record<string, unknown>, keys: readonly string[]): Record<string, unknown> {
  const output: Record<string, unknown> = {}
  for (const key of keys) {
    if (value[key] !== undefined) output[key] = typeof value[key] === 'string' ? assertSafeText(value[key], `event.${key}`, 1024) : value[key]
  }
  return output
}

function isSafeId(value: unknown): value is string {
  return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)
}
