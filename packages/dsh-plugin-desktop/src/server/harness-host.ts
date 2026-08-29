import { createInterface } from 'node:readline'
import type { Readable, Writable } from 'node:stream'
import {
  createHarnessTaskSession,
} from '@dsh/agent-adapter/harness-task-session'
import {
  createHarnessProcessTransport,
  validateHostMessage,
  HARNESS_HOST_SESSION_SCHEMA,
  type HarnessAgentRequest,
  type HarnessProcessTransportOptions,
  type HarnessTaskStartPayload,
  type HarnessTransport,
} from '@dsh/agent-adapter/harness-bridge'
import type { HarnessAgentExecutor } from '@dsh/agent-adapter/harness-host-handler'
import { runCodexTurn } from './codex-chat'
import { selectHarnessExecutor } from './harness-executor-selection'

const HOST_REQUEST_ID = 'desktop-harness-host'

export interface DesktopHarnessHostOptions {
  input: Readable
  output: Writable
  sidecar: HarnessProcessTransportOptions
  model?: string
  reasoningEffort?: string
  /** Optional provider-neutral executor registry supplied by the host app. */
  executors?: Record<string, HarnessAgentExecutor | undefined>
  /** Compatibility default; environment/task selection still wins. */
  defaultExecutor?: string
  createTransport?: (options: HarnessProcessTransportOptions) => HarnessTransport
  execute?: (request: HarnessAgentRequest, context: { signal: AbortSignal; emit(payload: Record<string, unknown>): void }) => Promise<{ finalResponse?: Record<string, unknown>; errorCode?: string }>
}

/**
 * Node host process entrypoint for the desktop app.
 *
 * It owns provider credentials and the selected model. Harness only receives
 * redacted execution facts through the sidecar protocol; this process never
 * forwards tokens, provider payloads or a model-owned plan to the sidecar.
 */
export async function runDesktopHarnessHost(options: DesktopHarnessHostOptions): Promise<void> {
  const start = await readTaskStart(options.input)
  const transport = (options.createTransport ?? createHarnessProcessTransport)(options.sidecar)
  const selectedExecutor = options.execute === undefined
    ? selectHarnessExecutor({
      requestedExecutor: start.payload.agent_backend,
      configuredExecutor: process.env.DSH_HARNESS_EXECUTOR,
      defaultExecutor: options.defaultExecutor ?? 'codex',
      executors: {
        codex: (request, context) => executeWithCodex(request, context.signal, options),
        ...options.executors,
      },
    })
    : undefined
  const session = createHarnessTaskSession({
    transport,
    execute: options.execute ?? selectedExecutor!.execute,
  })
  const writeEvent = (payload: Record<string, unknown>) => writeHostMessage(options.output, {
    schema_version: HARNESS_HOST_SESSION_SCHEMA,
    type: 'session.event',
    request_id: HOST_REQUEST_ID,
    payload,
  })
  session.onEvent(writeEvent)
  try {
    const result = await session.start(start.payload, start.request_id)
    writeHostMessage(options.output, {
      schema_version: HARNESS_HOST_SESSION_SCHEMA,
      type: 'task.result',
      request_id: start.request_id,
      payload: result,
    })
  } catch {
    writeHostMessage(options.output, {
      schema_version: HARNESS_HOST_SESSION_SCHEMA,
      type: 'task.result',
      request_id: start.request_id,
      payload: { status: 'failed', error_code: 'desktop_harness_host_failed' },
    })
  } finally {
    session.dispose()
  }
}

async function executeWithCodex(
  request: HarnessAgentRequest,
  signal: AbortSignal,
  options: DesktopHarnessHostOptions,
): Promise<{ finalResponse: Record<string, unknown> }> {
  const turn = await runCodexTurn({
    sessionId: `harness-${request.role}-${request.worktree_path.split('/').at(-1) ?? 'task'}`,
    prompt: request.prompt,
    cwd: request.worktree_path,
    model: options.model,
    reasoningEffort: options.reasoningEffort,
    system: '你是 Harness 的执行模型。只执行当前请求中的已确认步骤，不重新规划、不扩大范围、不改变目标项目之外的内容。遇到不确定性只报告事实。',
    signal,
    onDelta: (text) => {
      // Deltas are facts for the local UI only; they are not used as a new plan.
      void text
    },
  })
  const response = request.output_contract.schema_version === 'none'
    ? { text: turn.text, thread_id: turn.threadId }
    : { schema_version: request.output_contract.schema_version, text: turn.text, thread_id: turn.threadId }
  return { finalResponse: response }
}

async function readTaskStart(input: Readable): Promise<{ request_id: string; payload: HarnessTaskStartPayload }> {
  const lines = createInterface({ input, crlfDelay: Infinity })
  try {
    for await (const line of lines) {
      if (line.trim() === '') continue
      const value: unknown = JSON.parse(line)
      const message = validateHostMessage(value)
      if (message.type !== 'task.start') throw new Error('Harness 任务启动帧无效')
      return { request_id: message.request_id, payload: message.payload as unknown as HarnessTaskStartPayload }
    }
  } catch {
    throw new Error('Harness 任务启动帧无效')
  } finally {
    lines.close()
  }
  throw new Error('Harness 任务启动帧缺失')
}

function writeHostMessage(output: Writable, message: Record<string, unknown>): void {
  output.write(`${JSON.stringify(message)}\n`)
}
