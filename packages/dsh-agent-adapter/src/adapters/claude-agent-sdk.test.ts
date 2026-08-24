import { describe, expect, it, vi } from 'vitest'
import { createClaudeAgentSdkAdapter, type ClaudeAgentSdkClientLike } from './claude-agent-sdk.js'

describe('Claude API Agent adapter', () => {
  it('translates message streaming, approval, usage and completion events', async () => {
    const client: ClaudeAgentSdkClientLike = {
      stream: vi.fn(async () => events([
        { type: 'message_start', message: { id: 'message-1' } },
        { type: 'content_block_delta', delta: { type: 'text_delta', text: 'hello' } },
        { type: 'approval_request', requestId: 'approval-1', tool: 'terminal' },
        { type: 'message_delta', delta: { stop_reason: 'end_turn' }, usage: { output_tokens: 3 } },
        { type: 'message_stop' },
      ])),
    }
    const session = await createClaudeAgentSdkAdapter({ client }).start({ sessionId: 'claude-session', prompt: 'hello', permission: 'request-approval', model: 'claude-test' })
    const output = []
    for await (const event of session.run()) output.push(event)
    expect(output.map((event) => event.type)).toEqual(['session.started', 'message.delta', 'approval.requested', 'usage.updated', 'session.completed'])
    expect(client.stream).toHaveBeenCalledWith(expect.objectContaining({ model: 'claude-test', messages: [{ role: 'user', content: 'hello' }] }), expect.objectContaining({ signal: expect.any(AbortSignal) }))
  })

  it('does not accept a browser or local credential path', async () => {
    const client: ClaudeAgentSdkClientLike = { stream: async () => events([{ type: 'message_stop' }]) }
    const session = await createClaudeAgentSdkAdapter({ client, credentialSource: 'secure-store-session' }).start({ sessionId: 'claude-session', prompt: 'x', permission: 'smart-approval' })
    const output = []
    for await (const event of session.run()) output.push(event)
    expect(JSON.stringify(output)).not.toContain('credential')
  })
})

async function* events(items: readonly unknown[]) {
  for (const item of items) yield item
}
