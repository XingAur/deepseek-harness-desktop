import type { AgentAdapter, AgentAdapterSession, AgentStartRequest } from './contracts.js'
import { AdapterProtocolError, asRecord, assertNotAborted, assertSafeText, event, toAsyncIterable } from './contracts.js'

export interface ClaudeCliDescriptor {
  adapterKind: 'claude-cli-dev'
  experimental: true
  subscriptionReuse: false
  executable: string
}

export interface ClaudeCliDevAdapterOptions {
  developerMode: boolean
  executable: string
  run(request: { executable: string; prompt: string; permission: AgentStartRequest['permission']; signal: AbortSignal }): AsyncIterable<string> | Promise<AsyncIterable<string>>
}

export function getClaudeCliDescriptor(options: { developerMode: boolean; executable?: string }): ClaudeCliDescriptor | null {
  if (options.developerMode !== true || options.executable === undefined || options.executable.length === 0) return null
  return { adapterKind: 'claude-cli-dev', experimental: true, subscriptionReuse: false, executable: options.executable }
}

export function createClaudeCliDevAdapter(options: ClaudeCliDevAdapterOptions): AgentAdapter {
  if (!options.developerMode) throw new AdapterProtocolError('developer-mode-required', 'Claude CLI adapter requires developer mode')
  if (options.executable.trim() === '') throw new AdapterProtocolError('executable-required', 'Claude CLI executable is required')
  return {
    adapterKind: 'claude-cli-dev',
    async start(request) {
      const controller = new AbortController()
      request.signal?.addEventListener('abort', () => controller.abort(), { once: true })
      return {
        async *run() {
          assertNotAborted(controller.signal)
          yield event('session.started')
          const output = await toAsyncIterable(options.run({ executable: options.executable, prompt: request.prompt, permission: request.permission, signal: controller.signal }))
          let completed = false
          for await (const line of output) {
            assertNotAborted(controller.signal)
            if (line.length > 32 * 1024) throw new AdapterProtocolError('oversized-frame', 'Claude CLI output frame exceeds 32 KiB')
            let mapped: ReturnType<typeof mapCliEvent>
            try { mapped = mapCliEvent(JSON.parse(line)) } catch (cause) {
              if (cause instanceof AdapterProtocolError) throw cause
              throw new AdapterProtocolError('malformed-jsonl', 'Claude CLI structured output is invalid JSON')
            }
            if (mapped === null) continue
            if (mapped.type === 'session.completed' || mapped.type === 'session.failed') completed = true
            yield mapped
          }
          if (!completed) yield event('session.completed')
        },
        async cancel() { controller.abort() },
      } satisfies AgentAdapterSession
    },
  }
}

function mapCliEvent(raw: unknown) {
  const value = asRecord(raw)
  const type = assertSafeText(value.type, 'event.type', 128)
  switch (type) {
    case 'message.delta': return event('message.delta', { text: assertSafeText(value.text, 'event.text') })
    case 'approval.requested': return event('approval.requested', { requestId: assertSafeText(value.requestId, 'event.requestId', 128) })
    case 'session.completed': return event('session.completed')
    case 'session.failed': return event('session.failed')
    default: throw new AdapterProtocolError('unsupported-event', `Claude CLI event is not supported: ${type}`)
  }
}
