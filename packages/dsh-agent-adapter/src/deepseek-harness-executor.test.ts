import { describe, expect, it, vi } from 'vitest'
import type { ProviderAdapter } from './providers/contracts.js'
import { createDeepSeekExecutor } from './deepseek-harness-executor.js'

function request(role: 'worker' | 'reviewer' = 'reviewer') {
  return {
    schema_version: 'his-agent-backend-request.v1' as const,
    role,
    worktree_path: '/workspace/project',
    prompt: '请复核当前变更是否符合 Harness 决策。',
    timeout_seconds: 30,
    output_contract: { name: 'review', schema_version: 'deepseek-review.v1' },
    capabilities: ['read:workspace'],
  }
}

describe('DeepSeek Harness unified executor', () => {
  it('streams the selected DeepSeek model for review and returns the requested contract', async () => {
    const adapter: ProviderAdapter = {
      providerId: 'deepseek',
      async *stream(input) {
        expect(input.model).toBe('deepseek-chat')
        expect(input.apiKey).toBe('key-is-held-by-host')
        expect(input.messages).toEqual([{ role: 'user', content: request().prompt }])
        yield { type: 'message.delta', text: '结论：' }
        yield { type: 'message.delta', text: '符合。' }
      },
    }
    const emit = vi.fn()
    const execute = createDeepSeekExecutor({ adapter, apiKey: 'key-is-held-by-host', model: 'deepseek-chat' })

    await expect(execute(request(), { signal: new AbortController().signal, emit })).resolves.toEqual({
      finalResponse: { schema_version: 'deepseek-review.v1', text: '结论：符合。' },
    })
    expect(emit).toHaveBeenNthCalledWith(1, { type: 'message.delta', text: '结论：' })
    expect(emit).toHaveBeenNthCalledWith(2, { type: 'message.delta', text: '符合。' })
  })

  it('uses the same selected model for worker requests instead of forcing a reviewer-only role', async () => {
    const adapter: ProviderAdapter = {
      providerId: 'deepseek',
      async *stream(input) {
        expect(input.model).toBe('deepseek-reasoner')
        yield { type: 'message.delta', text: '生成受 Harness 契约约束的修改建议。' }
      },
    }
    const execute = createDeepSeekExecutor({ adapter, apiKey: 'host-secret', model: 'deepseek-reasoner' })

    await expect(execute(request('worker'), { signal: new AbortController().signal, emit: vi.fn() })).resolves.toEqual({
      finalResponse: { schema_version: 'deepseek-review.v1', text: '生成受 Harness 契约约束的修改建议。' },
    })
  })
})
