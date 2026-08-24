import { describe, expect, it } from 'vitest'
import { createClaudeCliDevAdapter, getClaudeCliDescriptor } from './claude-cli-dev.js'

describe('developer-only Claude CLI adapter', () => {
  it('is absent in stable mode and does not advertise subscription reuse', () => {
    expect(getClaudeCliDescriptor({ developerMode: false })).toBeNull()
    expect(getClaudeCliDescriptor({ developerMode: true, executable: '/usr/local/bin/claude' })).toMatchObject({ adapterKind: 'claude-cli-dev', experimental: true, subscriptionReuse: false })
  })

  it('requires an explicit executable and maps structured output through the same event contract', async () => {
    const adapter = createClaudeCliDevAdapter({ developerMode: true, executable: '/usr/local/bin/claude', run: async function* () {
      yield JSON.stringify({ type: 'message.delta', text: 'dev' })
      yield JSON.stringify({ type: 'session.completed' })
    } })
    const session = await adapter.start({ sessionId: 'dev-session', prompt: 'hello', permission: 'full-access' })
    const output = []
    for await (const event of session.run()) output.push(event)
    expect(output).toEqual([{ type: 'session.started', payload: {} }, { type: 'message.delta', payload: { text: 'dev' } }, { type: 'session.completed', payload: {} }])
  })

  it('rejects stable mode and missing executables', async () => {
    expect(() => createClaudeCliDevAdapter({ developerMode: false, executable: '/usr/local/bin/claude', run: async function* () {} })).toThrow(/developer mode/i)
    expect(() => createClaudeCliDevAdapter({ developerMode: true, executable: '', run: async function* () {} })).toThrow(/executable/i)
  })
})
