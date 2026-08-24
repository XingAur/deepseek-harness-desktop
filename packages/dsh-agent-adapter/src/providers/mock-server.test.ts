import { describe, expect, it, vi } from 'vitest'
import { createAnthropicAdapter } from './anthropic.js'
import { createOpenAICompatibleAdapter } from './openai-compatible.js'
import type { ProviderRequest } from './contracts.js'

const request: ProviderRequest = {
  model: 'test-model',
  apiKey: 'test-key',
  messages: [{ role: 'user', content: 'hello' }],
}

describe('provider adapters', () => {
  it('translates OpenAI-compatible SSE into bounded unified events', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response([
      'data: {"choices":[{"delta":{"content":"hel"}}]}',
      '',
      'data: {"choices":[{"delta":{"content":"lo"}}]}',
      '',
      'data: {"choices":[{"finish_reason":"stop"}],"usage":{"prompt_tokens":2,"completion_tokens":1}}',
      '',
      'data: [DONE]',
      '',
    ].join('\n'), { status: 200, headers: { 'content-type': 'text/event-stream' } }))
    const events = []
    for await (const event of createOpenAICompatibleAdapter({ baseUrl: 'https://api.example.test/v1' }).stream(request)) events.push(event)
    expect(events).toEqual([
      { type: 'message.delta', text: 'hel' },
      { type: 'message.delta', text: 'lo' },
      { type: 'message.completed', finishReason: 'stop' },
      { type: 'usage.updated', usage: { inputTokens: 2, outputTokens: 1 } },
    ])
    expect(fetchMock).toHaveBeenCalledWith('https://api.example.test/v1/chat/completions', expect.objectContaining({ redirect: 'error' }))
    fetchMock.mockRestore()
  })

  it('translates Claude text deltas and rejects non-HTTPS stable endpoints', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response([
      'event: content_block_delta',
      'data: {"delta":{"type":"text_delta","text":"hello"}}',
      '',
      'event: message_stop',
      'data: {}',
      '',
    ].join('\n'), { status: 200, headers: { 'content-type': 'text/event-stream' } }))
    const events = []
    for await (const event of createAnthropicAdapter({ baseUrl: 'https://api.anthropic.example.test' }).stream(request)) events.push(event)
    expect(events).toEqual([{ type: 'message.delta', text: 'hello' }, { type: 'message.completed', finishReason: 'stop' }])
    expect(fetchMock).toHaveBeenCalledWith('https://api.anthropic.example.test/v1/messages', expect.objectContaining({ redirect: 'error' }))
    fetchMock.mockRestore()

    const invalid = createOpenAICompatibleAdapter({ baseUrl: 'http://example.test/v1' })
    await expect((async () => { for await (const _event of invalid.stream(request)) undefined })()).rejects.toMatchObject({ code: 'redirect-rejected' })
  })
})
