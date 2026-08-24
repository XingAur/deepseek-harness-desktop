import type { AgentAdapter, AgentAdapterSession, AgentStartRequest, AdapterSemanticEvent } from './contracts.js'
import { AdapterProtocolError, asRecord, assertNotAborted, assertSafeText, event, toAsyncIterable } from './contracts.js'

export interface ClaudeAgentSdkClientLike {
  stream(input: { model?: string; max_tokens: number; messages: Array<{ role: 'user'; content: string }>; system?: string }, options: { signal: AbortSignal }): AsyncIterable<unknown> | Promise<AsyncIterable<unknown>>
}

export interface ClaudeAgentSdkAdapterOptions {
  client: ClaudeAgentSdkClientLike
  credentialSource?: 'secure-store-session'
}

export function createClaudeAgentSdkAdapter(options: ClaudeAgentSdkAdapterOptions): AgentAdapter {
  if (options.credentialSource !== undefined && options.credentialSource !== 'secure-store-session') throw new AdapterProtocolError('credential-source-rejected', 'Claude API Agent only accepts a secure-store session credential')
  return {
    adapterKind: 'claude-agent-sdk',
    async start(request): Promise<AgentAdapterSession> {
      const controller = new AbortController()
      request.signal?.addEventListener('abort', () => controller.abort(), { once: true })
      return {
        async *run() {
          assertNotAborted(controller.signal)
          yield event('session.started')
          const stream = await toAsyncIterable(options.client.stream({
            ...(request.model === undefined ? {} : { model: request.model }),
            max_tokens: 4096,
            messages: [{ role: 'user', content: request.prompt }],
          }, { signal: controller.signal }))
          let completed = false
          for await (const raw of stream) {
            assertNotAborted(controller.signal)
            const mapped = mapClaudeEvent(raw)
            if (mapped === null) continue
            if (mapped.type === 'session.completed' || mapped.type === 'session.failed') completed = true
            yield mapped
          }
          if (!completed) yield event('session.completed')
        },
        async cancel() { controller.abort() },
      }
    },
  }
}

function mapClaudeEvent(raw: unknown): AdapterSemanticEvent | null {
  const value = asRecord(raw)
  const type = assertSafeText(value.type, 'event.type', 128)
  switch (type) {
    case 'message_start': return null
    case 'content_block_delta': {
      const delta = asRecord(value.delta, 'event.delta')
      return delta.type === 'text_delta' ? event('message.delta', { text: assertSafeText(delta.text, 'event.delta.text') }) : null
    }
    case 'approval_request': return event('approval.requested', pickSafe(value, ['requestId', 'tool', 'summary']))
    case 'message_delta': {
      const usage = value.usage === undefined ? undefined : asRecord(value.usage, 'event.usage')
      return usage === undefined ? null : event('usage.updated', pickSafe(usage, ['input_tokens', 'output_tokens']))
    }
    case 'message_stop': return event('session.completed')
    case 'error': throw new AdapterProtocolError('provider-error', assertSafeText(value.message ?? 'Claude API Agent failed', 'event.message'))
    default: throw new AdapterProtocolError('unsupported-event', `Claude SDK event is not supported: ${type}`)
  }
}

function pickSafe(value: Record<string, unknown>, keys: readonly string[]): Record<string, unknown> {
  const output: Record<string, unknown> = {}
  for (const key of keys) if (value[key] !== undefined) output[key] = typeof value[key] === 'string' ? assertSafeText(value[key], `event.${key}`, 1024) : value[key]
  return output
}
