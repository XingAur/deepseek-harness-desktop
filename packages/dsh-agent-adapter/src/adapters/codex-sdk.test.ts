import { describe, expect, it, vi } from 'vitest'
import { createCodexSdkAdapter, type CodexSdkClientLike } from './codex-sdk.js'
import { codexEventFixtures } from './codex-fixtures.js'

describe('Codex SDK adapter', () => {
  it('creates a thread, maps documented events, and records the Harness mapping', async () => {
    const mapping = new Map<string, string>()
    const client: CodexSdkClientLike = {
      createThread: vi.fn(async () => ({ id: 'thread-1', events: asyncEvents(codexEventFixtures) })),
    }
    const adapter = createCodexSdkAdapter({ client, mapping })
    const session = await adapter.start({ sessionId: 'session-1', prompt: 'inspect', permission: 'request-approval' })
    const events = []
    for await (const event of session.run()) events.push(event)

    expect(mapping.get('session-1')).toBe('thread-1')
    expect(events.map((event) => event.type)).toEqual([
      'session.started', 'message.delta', 'approval.requested', 'file.diff.available', 'usage.updated', 'session.completed',
    ])
    expect(events[1]).toMatchObject({ payload: { text: 'hello' } })
    expect(events[3]).toMatchObject({ payload: { contentRef: { id: 'diff-1' } } })
  })

  it('resumes a mapped thread and forwards cancellation without retaining secrets', async () => {
    const client: CodexSdkClientLike = {
      resumeThread: vi.fn(async () => ({ id: 'thread-resume', events: asyncEvents([{ type: 'task.completed' }]) })),
      cancelThread: vi.fn(async () => undefined),
    }
    const adapter = createCodexSdkAdapter({ client, mapping: new Map([['session-2', 'thread-resume']]) })
    const session = await adapter.start({ sessionId: 'session-2', prompt: 'resume', permission: 'smart-approval', resume: true })
    const events = []
    for await (const event of session.run()) events.push(event)
    await session.cancel()

    expect(client.resumeThread).toHaveBeenCalledWith('thread-resume', expect.objectContaining({ prompt: 'resume' }))
    expect(client.cancelThread).toHaveBeenCalledWith('thread-resume')
    expect(JSON.stringify(events)).not.toContain('api')
  })

  it('fails closed on undocumented SDK event types', async () => {
    const client: CodexSdkClientLike = { createThread: vi.fn(async () => ({ id: 'thread-3', events: asyncEvents([{ type: 'unknown.event' }]) })) }
    const session = await createCodexSdkAdapter({ client, mapping: new Map() }).start({ sessionId: 'session-3', prompt: 'x', permission: 'full-access' })
    await expect(async () => { for await (const _event of session.run()) undefined }).rejects.toMatchObject({ code: 'unsupported-event' })
  })
})

async function* asyncEvents(events: readonly unknown[]) {
  for (const event of events) yield event
}
