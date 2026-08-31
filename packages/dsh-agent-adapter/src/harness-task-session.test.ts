import { describe, expect, it } from 'vitest'
import {
  createHarnessTaskSession,
  type HarnessTaskSession,
} from './harness-task-session.js'
import {
  HARNESS_HOST_SESSION_SCHEMA,
  type HarnessHostMessage,
  type HarnessTransport,
} from './harness-bridge.js'

class FakeTransport implements HarnessTransport {
  readonly sent: HarnessHostMessage[] = []
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

const task = {
  schema_version: 'harness-external-task.v1' as const,
  task_contract_path: '/private/tmp/harness-task.json',
  understanding_path: '/private/tmp/harness-understanding.json',
  worktree_root: '/private/tmp/his_harness_stage_f_task',
  knowledge_home: '/private/tmp/his_harness_knowledge_task',
  authorization_id: 'desktop-task-001',
}

describe('Harness task session', () => {
  it('turns Harness agent requests into execute-only model results', async () => {
    const transport = new FakeTransport()
    const session = createHarnessTaskSession({
      transport,
      execute: async (request, context) => {
        expect(request.prompt).toBe('execute-only')
        context.emit({ type: 'execution.started' })
        return { finalResponse: { ok: true } }
      },
    })

    const pending = session.start(task, 'task-1')
    expect(transport.sent[0]).toMatchObject({ type: 'task.start', request_id: 'task-1' })

    transport.deliver({
      schema_version: HARNESS_HOST_SESSION_SCHEMA,
      type: 'agent.request',
      request_id: 'agent-1',
      payload: {
        schema_version: 'his-agent-backend-request.v1',
        role: 'worker',
        worktree_path: '/private/tmp/his_harness_stage_f_task/run_1',
        prompt: 'execute-only',
        timeout_seconds: 30,
        output_contract: { name: 'none', schema_version: 'none' },
        capabilities: ['source.search'],
      },
    })
    await new Promise<void>((resolve) => setTimeout(resolve, 0))

    expect(transport.sent.map((message) => message.type)).toEqual(['task.start', 'session.event', 'agent.result'])
    expect(transport.sent[2].request_id).toBe('agent-1')
    expect(transport.sent[2].payload).toMatchObject({ error_code: '', final_response_validated: true })

    transport.deliver({
      schema_version: HARNESS_HOST_SESSION_SCHEMA,
      type: 'task.result',
      request_id: 'task-1',
      payload: { status: 'completed', error_code: '' },
    })

    await expect(pending).resolves.toEqual({ status: 'completed', error_code: '' })
    session.dispose()
  })

  it('does not create a second model plan when execution fails', async () => {
    const transport = new FakeTransport()
    const replans: string[] = []
    const session: HarnessTaskSession = createHarnessTaskSession({
      transport,
      execute: async () => {
        replans.push('model-executed')
        throw new Error('model failed')
      },
    })

    const pending = session.start(task, 'task-2')
    transport.deliver({
      schema_version: HARNESS_HOST_SESSION_SCHEMA,
      type: 'agent.request',
      request_id: 'agent-2',
      payload: {
        schema_version: 'his-agent-backend-request.v1',
        role: 'reviewer',
        worktree_path: '/private/tmp/his_harness_stage_f_task/run_1',
        prompt: 'review-only',
        timeout_seconds: 30,
        output_contract: { name: 'none', schema_version: 'none' },
        capabilities: ['source.read'],
      },
    })
    await new Promise<void>((resolve) => setTimeout(resolve, 0))
    expect(replans).toEqual(['model-executed'])
    expect(transport.sent.at(-1)?.type).toBe('agent.result')
    expect(transport.sent.at(-1)?.payload).toMatchObject({ error_code: 'worker_process_failed' })

    transport.deliver({
      schema_version: HARNESS_HOST_SESSION_SCHEMA,
      type: 'task.result',
      request_id: 'task-2',
      payload: { status: 'failed', error_code: 'external_task_execution_failed' },
    })
    await expect(pending).resolves.toMatchObject({ status: 'failed' })
    session.dispose()
  })
})
