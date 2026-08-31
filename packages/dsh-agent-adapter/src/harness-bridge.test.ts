import { describe, expect, it } from 'vitest'
import {
  HarnessBridgeClient,
  HARNESS_HOST_SESSION_SCHEMA,
  type HarnessAgentResult,
  type HarnessHostMessage,
  type HarnessTransport,
} from './harness-bridge.js'

const result: HarnessAgentResult = {
  schema_version: 'his-agent-backend-result.v1',
  exit_code: 0,
  error_code: '',
  event_count: 1,
  final_response_sha256: 'a'.repeat(64),
  canonical_final_response_sha256: 'b'.repeat(64),
  final_response_validated: true,
  final_response: { ok: true },
}

class FakeTransport implements HarnessTransport {
  sent: HarnessHostMessage[] = []
  private listener: ((message: unknown) => void) | undefined

  send(message: HarnessHostMessage): void {
    this.sent.push(message)
  }

  onMessage(listener: (message: unknown) => void): () => void {
    this.listener = listener
    return () => { this.listener = undefined }
  }

  deliver(message: unknown): void {
    this.listener?.(message)
  }
}

describe('HarnessBridgeClient', () => {
  it('rejects a response with a mismatched request id', async () => {
    const transport = new FakeTransport()
    const client = new HarnessBridgeClient(transport)
    const pending = client.awaitAgentResult('req-1', 100)

    transport.deliver({
      schema_version: HARNESS_HOST_SESSION_SCHEMA,
      type: 'agent.result',
      request_id: 'other',
      payload: result,
    })

    await expect(pending).rejects.toThrow('请求关联失败')
  })

  it('returns only a validated result and never serializes secret-shaped fields', async () => {
    const transport = new FakeTransport()
    const client = new HarnessBridgeClient(transport)
    const pending = client.awaitAgentResult('req-1', 100)

    transport.deliver({
      schema_version: HARNESS_HOST_SESSION_SCHEMA,
      type: 'agent.result',
      request_id: 'req-1',
      payload: result,
    })

    await expect(pending).resolves.toEqual(result)
    expect(JSON.stringify(transport.sent)).not.toContain('token')
  })

  it('dispatches Harness agent requests to the host without changing the request', async () => {
    const transport = new FakeTransport()
    const client = new HarnessBridgeClient(transport)
    const seen: Array<{ request: unknown; requestId: string }> = []
    client.onAgentRequest((request, requestId) => {
      seen.push({ request, requestId })
    })
    const request = {
      schema_version: 'his-agent-backend-request.v1',
      role: 'worker',
      worktree_path: '/private/tmp/his_harness_stage_f_task',
      prompt: 'execute-only',
      timeout_seconds: 30,
      output_contract: { name: 'none', schema_version: 'none' },
      capabilities: ['source.search'],
    }

    transport.deliver({ schema_version: HARNESS_HOST_SESSION_SCHEMA, type: 'agent.request', request_id: 'agent-1', payload: request })

    expect(seen).toEqual([{ request, requestId: 'agent-1' }])
  })

  it('fails closed when an agent request payload is malformed', async () => {
    const transport = new FakeTransport()
    const client = new HarnessBridgeClient(transport)
    const pending = client.awaitAgentResult('agent-1', 100)

    transport.deliver({
      schema_version: HARNESS_HOST_SESSION_SCHEMA,
      type: 'agent.request',
      request_id: 'agent-1',
      payload: { schema_version: 'his-agent-backend-request.v1', role: 'worker' },
    })

    await expect(pending).rejects.toThrow('Harness Agent 请求无效')
  })

  it('correlates a task result separately from an agent result', async () => {
    const transport = new FakeTransport()
    const client = new HarnessBridgeClient(transport)
    const pending = client.awaitTaskResult('task-1', 100)

    transport.deliver({
      schema_version: HARNESS_HOST_SESSION_SCHEMA,
      type: 'task.result',
      request_id: 'task-1',
      payload: { status: 'completed', error_code: '' },
    })

    await expect(pending).resolves.toEqual({ status: 'completed', error_code: '' })
  })

  it('accepts a read-only Yunxiao intake task through the normal task protocol', () => {
    const transport = new FakeTransport()
    const client = new HarnessBridgeClient(transport)
    const payload = {
      schema_version: 'harness-external-task.v1' as const,
      archive_root: '/private/tmp/harness-archive',
      intake_source: 'DFHIS-39999',
      intake_include_comments: true,
      worktree_root: '/private/tmp/harness-worktree',
      knowledge_home: '/private/tmp/harness-knowledge',
      authorization_id: 'harness-intake',
    }

    expect(() => client.startTask(payload, 'intake-1')).not.toThrow()
    expect(transport.sent[0]).toMatchObject({
      type: 'task.start',
      request_id: 'intake-1',
      payload,
    })
  })
})
