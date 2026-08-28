import { listCodexModels, resolveCodexCli, runCodexTurn } from './server/codex-chat'

export const name = 'desktop-shell'
export const inject = ['llm', 'sessions']

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any

/**
 * Codex 聊天模型适配器：把 provider 路由 `codex` 注册进官方 LLM 服务
 * （`ctx.llm.registerAdapter`，鸭子类型契约，与 LlmAdapter 基类等价）。
 * 聊天模型选择器经 `llm/adapters-updated` 自动发现本路由。
 *
 * v1 行为：Codex 在会话工作目录内、官方沙箱（workspace-write）中自治
 * 执行；回复以 text-delta 实时流入聊天。同会话复用同一 Codex 线程。
 */

interface ModelInfo {
  provider: string
  id: string
  name: string
  description?: string
  reasoningEfforts?: string[]
  defaultReasoningEffort?: string
}

function createCodexAdapter(ctx: Any) {
  const adapter = {
    providerInfo(provider: string) {
      return { id: provider, name: 'Codex' }
    },

    async listModels(provider: string): Promise<ModelInfo[]> {
      try {
        const models = await listCodexModels()
        if (models.length > 0) {
          return models.map((model) => ({
            provider,
            id: model.id,
            name: model.name,
            description: model.description,
            reasoningEfforts: model.reasoningEfforts,
            defaultReasoningEffort: model.defaultReasoningEffort,
          }))
        }
      } catch {
        // 模型目录不可用时仍保留默认路由，发送时由 Codex 使用当前账户默认模型。
      }
      return [{
        provider,
        id: 'codex-default',
        name: 'Codex 默认模型',
        description: 'OpenAI Codex CLI：在会话目录的官方沙箱内执行任务并流式回复',
        reasoningEfforts: ['low', 'medium', 'high'],
        defaultReasoningEffort: 'medium',
      }]
    },

    resolveModel(provider: string, model: string): Promise<ModelInfo> {
      return Promise.resolve({
        provider,
        id: model,
        name: model === 'codex-default' ? 'Codex' : `Codex · ${model}`,
      })
    },

    async prepareCall(provider: string, model: string, _signal?: AbortSignal) {
      const resolved = await adapter.resolveModel(provider, model)
      return { model: resolved, stream: (options: unknown) => adapter.stream(options) }
    },

    async *stream(options: Any): AsyncGenerator<Any> {
      const sessionId = typeof options?.sessionId === 'string' && options.sessionId !== ''
        ? options.sessionId
        : `dsh-adhoc-${Date.now()}`
      const cwd = resolveSessionCwd(ctx, options)
      const prompt = flattenMessages(options?.messages)
      const model = typeof options?.model === 'string' && options.model !== '' && options.model !== 'codex-default'
        ? options.model
        : undefined
      const reasoningEffort = typeof options?.reasoningEffort === 'string' && options.reasoningEffort !== ''
        ? options.reasoningEffort
        : typeof options?.effort === 'string' && options.effort !== '' ? options.effort : undefined

      const chunks: string[] = []
      const queue = createDeltaQueue()
      const started = runCodexTurn({
        sessionId,
        prompt,
        cwd,
        model,
        reasoningEffort,
        system: typeof options?.system === 'string' ? options.system : undefined,
        signal: options?.signal,
        onDelta: (delta) => queue.push(delta),
      })
      started.then(
        () => queue.finish(undefined),
        (cause: unknown) => queue.finish(cause instanceof Error ? cause : new Error('Codex 请求失败')),
      )

      yield { type: 'block-start', index: 0, blockType: 'text' }
      try {
        for await (const delta of queue.drain()) {
          chunks.push(delta)
          yield { type: 'text-delta', index: 0, text: delta }
        }
        const text = chunks.join('')
        yield { type: 'block-end', index: 0, block: { type: 'text', text } }
        yield { type: 'finish', reason: { kind: 'stop' } }
      } catch (cause) {
        const message = cause instanceof Error && cause.message.length > 0 ? cause.message : 'Codex 请求失败'
        yield { type: 'finish', reason: { kind: 'error', failure: { message, code: 'CODEX_FAILED' } } }
      }
    },
  }
  return adapter
}

/** 把回调式增量桥接为异步迭代器，保证聊天里的实时流式呈现。 */
function createDeltaQueue() {
  const pending: string[] = []
  let woken: (() => void) | null = null
  let finished = false
  let failure: Error | undefined
  return {
    push(chunk: string): void {
      pending.push(chunk)
      woken?.()
    },
    finish(error?: Error): void {
      finished = true
      failure = error
      woken?.()
    },
    async *drain(): AsyncGenerator<string> {
      while (true) {
        if (pending.length > 0) {
          yield pending.shift() as string
          continue
        }
        if (finished) {
          if (failure !== undefined) throw failure
          return
        }
        await new Promise<void>((resolve) => { woken = () => { woken = null; resolve() } })
      }
    },
  }
}

interface MessageLike {
  role?: string
  content?: Array<{ type?: string; text?: string }>
}

function flattenMessages(messages: unknown): string {
  if (!Array.isArray(messages)) return ''
  const parts: string[] = []
  for (const message of messages as MessageLike[]) {
    if (typeof message !== 'object' || message === null) continue
    const text = Array.isArray(message.content)
      ? message.content
        .filter((block) => block?.type === 'text' && typeof block.text === 'string')
        .map((block) => block.text as string)
        .join('')
      : ''
    if (text.trim() === '') continue
    parts.push(message.role === 'user' ? text : `[${message.role ?? 'assistant'}]\n${text}`)
  }
  return parts.join('\n\n')
}

/** 会话工作目录：会话头里的 cwd（官方 agent loop 同源），最后退回 HOME。 */
function resolveSessionCwd(ctx: Any, options: Any): string {
  const sessionId = typeof options?.sessionId === 'string' ? options.sessionId : ''
  if (sessionId !== '') {
    try {
      const header = ctx.sessions?.get?.(sessionId)?.header
      if (typeof header?.cwd === 'string' && header.cwd.startsWith('/')) return header.cwd
    } catch {
      // 会话不可达时走回退
    }
  }
  const home = process.env.HOME
  if (typeof home === 'string' && home.startsWith('/') && home.length > 1) return home
  return process.cwd()
}

export function apply(ctx: Any): void {
  ctx.llm.registerAdapter(['codex'], createCodexAdapter(ctx))
  const log = ctx.logger?.info?.bind(ctx.logger)
  log?.('desktop-shell: codex chat model 已注册')
  void resolveCodexCli()
}
