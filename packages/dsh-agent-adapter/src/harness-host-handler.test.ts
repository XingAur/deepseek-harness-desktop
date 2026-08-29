import { describe, expect, it } from 'vitest'
import { createHarnessHostHandler, type HarnessAgentRequest } from './harness-host-handler.js'

const request: HarnessAgentRequest = {
  schema_version: 'his-agent-backend-request.v1',
  role: 'worker',
  worktree_path: '/private/tmp/his_harness_stage_f_task',
  prompt: '只执行 Harness 已确认的方案，不重新规划。',
  timeout_seconds: 30,
  output_contract: { name: 'none', schema_version: 'none' },
  capabilities: ['source.search'],
}

describe('Harness host handler', () => {
  it('forwards the exact execute-only request and returns canonical digests', async () => {
    const seen: HarnessAgentRequest[] = []
    const events: Record<string, unknown>[] = []
    const handler = createHarnessHostHandler({
      execute: async (received, context) => {
        seen.push(received)
        context.emit({ type: 'execution.started' })
        return { finalResponse: { ok: true, text: '已按 Harness 决策执行' } }
      },
    })

    const result = await handler(request, (payload) => events.push(payload))

    expect(seen).toEqual([request])
    expect(events).toEqual([{ type: 'execution.started' }])
    expect(result.exit_code).toBe(0)
    expect(result.error_code).toBe('')
    expect(result.final_response_validated).toBe(true)
    expect(result.final_response).toEqual({ ok: true, text: '已按 Harness 决策执行' })
    expect(result.final_response_sha256).toMatch(/^[0-9a-f]{64}$/)
    expect(result.canonical_final_response_sha256).toBe(result.final_response_sha256)
  })

  it('converts executor failures to a bounded code without leaking the failure text', async () => {
    const handler = createHarnessHostHandler({
      execute: async () => {
        throw new Error('provider token=secret-value and raw_payload must not escape')
      },
    })

    const result = await handler(request)

    expect(result.exit_code).toBe(1)
    expect(result.error_code).toBe('worker_process_failed')
    expect(JSON.stringify(result)).not.toContain('secret-value')
    expect(JSON.stringify(result)).not.toContain('raw_payload')
  })

  it('rejects a model response that does not satisfy the Harness output contract', async () => {
    const reviewerRequest = { ...request, role: 'reviewer' as const, output_contract: { name: 'his-local-agent-review', schema_version: 'his-local-agent-review.v1' } }
    const handler = createHarnessHostHandler({
      execute: async () => ({ finalResponse: { verdict: 'approved' } }),
    })

    const result = await handler(reviewerRequest)

    expect(result.exit_code).toBe(1)
    expect(result.error_code).toBe('worker_protocol_invalid')
    expect(result.final_response_validated).toBe(false)
  })
})
