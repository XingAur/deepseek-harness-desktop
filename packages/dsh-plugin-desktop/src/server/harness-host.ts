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
import { createDeepSeekExecutor } from '@dsh/agent-adapter/deepseek-harness-executor'
import { createDeepSeekAdapter, createOpenAICompatibleAdapter } from '@dsh/agent-adapter/providers/openai-compatible'
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
      requestedExecutor: start.payload.agent_backend ?? executorHintForModel(start.payload.selected_model_id),
      configuredExecutor: process.env.DSH_HARNESS_EXECUTOR,
      defaultExecutor: options.defaultExecutor ?? 'codex',
      executors: {
        codex: (request, context) => executeWithCodex(request, context.signal, options, start.payload.selected_model_id),
        ...configuredDeepSeekExecutor(process.env, start.payload.selected_model_id),
        ...configuredOpenAICompatibleExecutor(process.env, start.payload.selected_model_id),
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

/**
 * Register the selected DeepSeek model for every Harness role. Credentials
 * arrive from the Tauri secure vault through a host-only env var; they are
 * never put into the Harness sidecar protocol.
 */
export function configuredDeepSeekExecutor(
  env: NodeJS.ProcessEnv = process.env,
  selectedModelId?: string,
): Record<string, HarnessAgentExecutor> {
  const apiKey = env.DSH_DEEPSEEK_API_KEY?.trim()
  if (apiKey === undefined || apiKey.length === 0) return {}
  return {
    deepseek: createDeepSeekExecutor({
      adapter: createDeepSeekAdapter(),
      apiKey,
      model: selectedModelId?.trim() || env.DSH_DEEPSEEK_MODEL?.trim() || 'deepseek-chat',
    }),
  }
}

/** Compatibility export for older host integrations. */
export const configuredDeepSeekReviewerExecutor = configuredDeepSeekExecutor

/**
 * Register an OpenAI-compatible executor for any provider exposing the
 * /chat/completions protocol (OpenAI, Qwen, GLM, Kimi, local gateways …).
 * The base URL comes from the desktop host environment; the model id is the
 * task-selected model. No hardcoded allowlist: any model the configured
 * endpoint serves can be selected.
 */
export function configuredOpenAICompatibleExecutor(
  env: NodeJS.ProcessEnv = process.env,
  selectedModelId?: string,
): Record<string, HarnessAgentExecutor> {
  const apiKey = env.DSH_OPENAI_API_KEY?.trim()
  const baseUrl = env.DSH_OPENAI_BASE_URL?.trim() || 'https://api.openai.com/v1'
  const model = selectedModelId?.trim()
  if (apiKey === undefined || apiKey.length === 0 || model === undefined || model.length === 0) return {}
  return {
    'openai-compatible': createDeepSeekExecutor({
      adapter: createOpenAICompatibleAdapter({ baseUrl }),
      apiKey,
      model,
    }),
  }
}

/**
 * Bind a provider-namespaced model id to its executor when the task did not
 * pick a backend explicitly.  This is provider routing, not a product
 * restriction: any other model keeps the configured/default executor.
 */
export function executorHintForModel(selectedModelId: string | undefined): string | undefined {
  const model = selectedModelId?.trim().toLowerCase()
  if (model === undefined || model === '') return undefined
  if (model.startsWith('deepseek')) return 'deepseek'
  return undefined
}

async function executeWithCodex(
  request: HarnessAgentRequest,
  signal: AbortSignal,
  options: DesktopHarnessHostOptions,
  selectedModelId?: string,
): Promise<{ finalResponse: Record<string, unknown> }> {
  const turn = await runCodexTurn({
    sessionId: `harness-${request.role}-${request.worktree_path.split('/').at(-1) ?? 'task'}`,
    prompt: request.prompt,
    cwd: request.worktree_path,
    model: selectedModelId?.trim() || options.model,
    reasoningEffort: options.reasoningEffort,
    system: '你是 Harness 的执行模型。只执行当前请求中的已确认步骤，不重新规划、不扩大范围、不改变目标项目之外的内容。遇到不确定性只报告事实。',
    signal,
    onDelta: (text) => {
      // Deltas are facts for the local UI only; they are not used as a new plan.
      void text
    },
  })
  const response = request.output_contract.schema_version === 'none'
    // The Harness side rejects opaque provider keys (thread_id etc.) in
    // final_response; only the plain text crosses the bridge.
    ? { text: turn.text }
    : { schema_version: request.output_contract.schema_version, text: turn.text }
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
