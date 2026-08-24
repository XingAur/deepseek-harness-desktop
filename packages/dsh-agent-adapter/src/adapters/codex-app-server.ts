import { AdapterProtocolError, asRecord, assertSafeText, event, type AgentAdapterSession, type AgentStartRequest, type AdapterSemanticEvent } from './contracts.js'

export interface AppServerTransport {
  send(message: Record<string, unknown>): Promise<void>
  receive(): AsyncGenerator<string>
  close(): Promise<void>
}

export interface CodexAppServerPreviewOptions {
  enabled: boolean
  version: string
  protocol: string
  transport: AppServerTransport
  sessionId: string
  prompt: string
  model?: string
}

export function isCodexAppServerPreviewCompatible(options: Pick<CodexAppServerPreviewOptions, 'enabled' | 'version' | 'protocol'>): boolean {
  return options.enabled && options.protocol === 'codex-app-server/v1' && isVersionInPreviewRange(options.version)
}

export async function createCodexAppServerPreview(options: CodexAppServerPreviewOptions): Promise<AgentAdapterSession> {
  if (!isCodexAppServerPreviewCompatible(options)) throw new AdapterProtocolError('preview-incompatible', 'Codex App Server preview is disabled or incompatible')
  await options.transport.send({ id: 1, method: 'thread.start', params: { sessionId: options.sessionId, prompt: options.prompt, ...(options.model === undefined ? {} : { model: options.model }) } })
  const receive = options.transport.receive()
  const first = await receive.next()
  if (first.done) throw new AdapterProtocolError('server-closed', 'Codex App Server closed before thread creation')
  const response = parseJsonLine(first.value)
  const result = asRecord(response.result, 'Codex App Server thread result')
  if (typeof result.threadId !== 'string' || result.threadId.length === 0) throw new AdapterProtocolError('malformed-response', 'Codex App Server did not return a thread id')

  return {
    async *run() {
      yield event('session.started')
      for await (const line of receive) {
        const notification = parseJsonLine(line)
        if (notification.method === undefined) continue
        const mapped = mapNotification(notification)
        if (mapped === null) continue
        yield mapped
        if (mapped.type === 'session.completed' || mapped.type === 'session.failed') return
      }
      throw new AdapterProtocolError('server-closed', 'Codex App Server closed before completion')
    },
    async cancel() {
      await options.transport.send({ id: 2, method: 'turn.cancel', params: { threadId: result.threadId } })
      await options.transport.close()
    },
  }
}

function mapNotification(value: Record<string, unknown>): AdapterSemanticEvent | null {
  const method = assertSafeText(value.method, 'notification.method', 128)
  const params = value.params === undefined ? {} : asRecord(value.params, 'notification.params')
  switch (method) {
    case 'message.delta': return event('message.delta', { text: assertSafeText(params.text, 'notification.params.text') })
    case 'approval.requested': return event('approval.requested', params)
    case 'turn.completed': return event('session.completed')
    case 'turn.failed': return event('session.failed')
    default: return null
  }
}

function parseJsonLine(line: string): Record<string, unknown> {
  if (line.length > 32 * 1024) throw new AdapterProtocolError('oversized-frame', 'Codex App Server frame exceeds 32 KiB')
  try { return asRecord(JSON.parse(line), 'Codex App Server frame') } catch (cause) {
    if (cause instanceof AdapterProtocolError && cause.code === 'malformed-event') throw new AdapterProtocolError('malformed-jsonl', 'Codex App Server frame is not an object')
    throw new AdapterProtocolError('malformed-jsonl', 'Codex App Server frame is not valid JSON')
  }
}

function isVersionInPreviewRange(value: string): boolean {
  const match = /^(\d+)\.(\d+)\.(\d+)(?:[-+].*)?$/.exec(value)
  if (match === null) return false
  const major = Number(match[1])
  return major === 1
}
