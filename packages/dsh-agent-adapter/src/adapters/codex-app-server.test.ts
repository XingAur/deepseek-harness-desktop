import { describe, expect, it } from 'vitest'
import { createCodexAppServerPreview, isCodexAppServerPreviewCompatible, type AppServerTransport } from './codex-app-server.js'

describe('Codex App Server preview adapter', () => {
  it('is disabled unless the explicit preview gate and exact protocol range pass', () => {
    expect(isCodexAppServerPreviewCompatible({ enabled: false, version: '1.2.3', protocol: 'codex-app-server/v1' })).toBe(false)
    expect(isCodexAppServerPreviewCompatible({ enabled: true, version: '0.9.0', protocol: 'codex-app-server/v1' })).toBe(false)
    expect(isCodexAppServerPreviewCompatible({ enabled: true, version: '1.2.3', protocol: 'codex-app-server/v1' })).toBe(true)
    expect(isCodexAppServerPreviewCompatible({ enabled: true, version: '1.2.3', protocol: 'codex-app-server/v2' })).toBe(false)
  })

  it('converts sanitized JSONL notifications and ignores unknown notifications', async () => {
    const transport = scriptedTransport([
      JSON.stringify({ id: 1, result: { threadId: 'thread-preview' } }),
      JSON.stringify({ method: 'message.delta', params: { text: 'preview' } }),
      JSON.stringify({ method: 'unknown.notification', params: { secret: 'must-not-leak' } }),
      JSON.stringify({ method: 'turn.completed', params: {} }),
    ])
    const session = await createCodexAppServerPreview({
      enabled: true,
      version: '1.2.3',
      protocol: 'codex-app-server/v1',
      transport,
      sessionId: 'preview-session',
      prompt: 'hello',
    })
    const events = []
    for await (const event of session.run()) events.push(event)
    expect(events).toEqual([
      { type: 'session.started', payload: {} },
      { type: 'message.delta', payload: { text: 'preview' } },
      { type: 'session.completed', payload: {} },
    ])
    expect(transport.sent[0]).toMatchObject({ method: 'thread.start', params: { sessionId: 'preview-session', prompt: 'hello' } })
    expect(JSON.stringify(events)).not.toContain('must-not-leak')
  })

  it('rejects malformed JSONL and falls back without deleting the task mapping', async () => {
    const transport = scriptedTransport(['not-json'])
    await expect(createCodexAppServerPreview({ enabled: true, version: '1.2.3', protocol: 'codex-app-server/v1', transport, sessionId: 'preview-session', prompt: 'hello' })).rejects.toMatchObject({ code: 'malformed-jsonl' })
  })
})

function scriptedTransport(lines: readonly string[]): AppServerTransport & { sent: Array<Record<string, unknown>> } {
  const sent: Array<Record<string, unknown>> = []
  return {
    sent,
    async send(message) { sent.push(message) },
    async *receive() { for (const line of lines) yield line },
    async close() {},
  }
}
