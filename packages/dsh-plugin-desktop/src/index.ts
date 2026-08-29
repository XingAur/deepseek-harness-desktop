import {
  listCodexModels,
  resolveCodexCli,
  runCodexTurn,
  type CodexUserInput,
} from './server/codex-chat'

export const name = 'desktop-shell'
export const inject = ['llm', 'sessions']

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any

/**
 * Codex 聊天模型适配器：把 provider 路由 `codex` 注册进官方 LLM 服务
 * （`ctx.llm.registerAdapter`，鸭子类型契约，与 LlmAdapter 基类等价）。
 * 聊天模型选择器经 `llm/adapters-updated` 自动发现本路由。
 *
 * 主聊天和 Agent 工作台共用 Codex 的权限映射；回复以 text-delta 实时
 * 流入聊天，同会话复用同一 Codex 线程，图片通过附件服务转换为 image input。
 */

interface ModelInfo {
  provider: string
  id: string
  name: string
  description?: string
  inputModalities?: string[]
  reasoning?: {
    efforts: Array<{ id: string; name: string; description?: string }>
    defaultEffort?: string
  }
}

interface MessageBlockLike {
  type?: string
  text?: string
  attachment?: unknown
}

interface MessageLike {
  role?: string
  content?: MessageBlockLike[]
}

interface AttachmentServiceLike {
  readImageRequest(
    reference: unknown,
    policy: { maxPixels: number; maxBytes: number },
    signal?: AbortSignal,
  ): Promise<{ data: Uint8Array; mediaType: string }>
}

const CODEX_IMAGE_POLICY = {
  maxPixels: 4_000_000,
  maxBytes: 4 * 1024 * 1024,
}

function createCodexAdapter(ctx: Any) {
  const modelCache = new Map<string, ModelInfo>()
  const cacheKey = (provider: string, model: string) => `${provider}\u0000${model}`
  const adapter = {
    providerInfo(provider: string) {
      return { id: provider, name: 'Codex' }
    },

    providerRetryPolicy(_provider: string) {
      // 使用 DSH 的默认重试策略；Codex app-server 自己维护一轮请求的状态。
      return undefined
    },

    async listModels(provider: string): Promise<ModelInfo[]> {
      try {
        const models = await listCodexModels()
        if (models.length > 0) {
          const resolved = models.map((model) => ({
            provider,
            id: model.id,
            name: model.name,
            description: model.description,
            inputModalities: model.inputModalities,
            reasoning: model.reasoning,
          }))
          for (const model of resolved) modelCache.set(cacheKey(provider, model.id), model)
          return resolved
        }
      } catch {
        // 模型目录不可用时仍保留默认路由，发送时由 Codex 使用当前账户默认模型。
      }
      const fallback = {
        provider,
        id: 'codex-default',
        name: 'Codex 默认模型',
        description: 'OpenAI Codex CLI：在会话目录的官方沙箱内执行任务并流式回复',
        inputModalities: ['text', 'image'],
      }
      modelCache.set(cacheKey(provider, fallback.id), fallback)
      return [fallback]
    },

    async resolveModel(provider: string, model: string): Promise<ModelInfo> {
      const cached = modelCache.get(cacheKey(provider, model))
      if (cached !== undefined) return cached
      const models = await adapter.listModels(provider)
      const discovered = models.find((candidate) => candidate.id === model)
      if (discovered !== undefined) return discovered
      return {
        provider,
        id: model,
        name: model === 'codex-default' ? 'Codex' : `Codex · ${model}`,
        inputModalities: ['text', 'image'],
      }
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
      const currentPrompt = flattenCurrentUserMessage(options?.messages)
      const selectedModel = typeof options?.model === 'string' && options.model !== ''
        ? options.model
        : 'codex-default'
      const model = selectedModel !== 'codex-default' ? selectedModel : undefined
      const reasoningEffort = typeof options?.reasoningEffort === 'string' && options.reasoningEffort !== ''
        ? options.reasoningEffort
        : typeof options?.effort === 'string' && options.effort !== ''
          ? options.effort
          : (await adapter.resolveModel('codex', selectedModel)).reasoning?.defaultEffort

      const chunks: string[] = []
      const queue = createDeltaQueue()
      yield { type: 'block-start', index: 0, blockType: 'text' }
      let input: CodexUserInput[]
      let currentInput: CodexUserInput[]
      try {
        const built = await buildCodexInputs(options?.messages, getAttachments(ctx), options?.signal)
        input = built.all
        currentInput = built.current
      } catch (cause) {
        const message = cause instanceof Error && cause.message.length > 0 ? cause.message : '图片附件读取失败'
        yield { type: 'finish', reason: { kind: 'error', failure: { message, code: 'CODEX_INPUT_FAILED' } } }
        return
      }
      const started = runCodexTurn({
        sessionId,
        prompt,
        input,
        currentPrompt,
        currentInput,
        cwd,
        model,
        reasoningEffort,
        permission: isCodexPermission(options?.permission) ? options.permission : 'request-approval',
        system: typeof options?.system === 'string' ? options.system : undefined,
        signal: options?.signal,
        onDelta: (delta) => queue.push(delta),
      })
      started.then(
        () => queue.finish(undefined),
        (cause: unknown) => queue.finish(cause instanceof Error ? cause : new Error('Codex 请求失败')),
      )

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

function flattenCurrentUserMessage(messages: unknown): string {
  if (!Array.isArray(messages)) return ''
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index] as MessageLike
    if (message?.role !== 'user') continue
    const text = Array.isArray(message.content)
      ? message.content
        .filter((block) => block?.type === 'text' && typeof block.text === 'string')
        .map((block) => block.text as string)
        .join('')
      : ''
    return text
  }
  return ''
}

function getAttachments(ctx: Any): AttachmentServiceLike | undefined {
  const service = ctx.attachments ?? ctx.get?.('attachments')
  return service !== null && typeof service === 'object' && typeof service.readImageRequest === 'function'
    ? service as AttachmentServiceLike
    : undefined
}

async function buildCodexInputs(
  messages: unknown,
  attachments: AttachmentServiceLike | undefined,
  signal?: AbortSignal,
): Promise<{ all: CodexUserInput[]; current: CodexUserInput[] }> {
  if (!Array.isArray(messages)) return { all: [], current: [] }
  const all: CodexUserInput[] = []
  const current: CodexUserInput[] = []
  const currentUserIndex = messages.reduce((found, message, index) => {
    return (message as MessageLike)?.role === 'user' ? index : found
  }, -1)
  for (const [index, message] of (messages as MessageLike[]).entries()) {
    if (typeof message !== 'object' || message === null || !Array.isArray(message.content)) continue
    const isCurrent = index === currentUserIndex
    for (const block of message.content) {
      if (block?.type === 'text' && typeof block.text === 'string' && block.text.trim() !== '') {
        const value: CodexUserInput = {
          type: 'text',
          text: message.role === 'user' ? block.text : `[${message.role ?? 'assistant'}]\n${block.text}`,
        }
        all.push(value)
        if (isCurrent) current.push(value)
        continue
      }
      if (block?.type !== 'image') continue
      if (attachments === undefined || block.attachment === undefined) {
        throw new Error('当前 Codex 图片输入不可用：没有找到会话附件服务。请重新上传图片后重试。')
      }
      const image = await attachments.readImageRequest(block.attachment, CODEX_IMAGE_POLICY, signal)
      const value: CodexUserInput = {
        type: 'image',
        url: `data:${image.mediaType};base64,${Buffer.from(image.data).toString('base64')}`,
      }
      all.push(value)
      if (isCurrent) current.push(value)
    }
  }
  return { all, current }
}

function isCodexPermission(value: unknown): value is 'request-approval' | 'smart-approval' | 'full-access' {
  return value === 'request-approval' || value === 'smart-approval' || value === 'full-access'
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
