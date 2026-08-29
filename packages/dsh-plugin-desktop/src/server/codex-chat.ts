import { spawn, type ChildProcess } from 'node:child_process'
import { existsSync } from 'node:fs'
import { homedir } from 'node:os'
import { extname, isAbsolute, join } from 'node:path'
import { permissionToCodexThreadOptions } from '@dsh/agent-adapter/adapters'

/**
 * Codex 聊天模型适配器：把官方 Codex CLI（app-server JSON-RPC over stdio）
 * 注册为 DeepSeek Harness 的一个 LLM 提供方路由（provider: codex）。
 *
 * 注册后，聊天界面的模型选择器会自动出现 Codex；每个会话映射到一个
 * Codex 线程（按 GenerateOptions.sessionId 记忆并 resume），回复以
 * text-delta 流式呈现。
 *
 * 权限边界：
 * - 主聊天和 Agent 工作台共用 request-approval / smart-approval /
 *   full-access 到 Codex sandbox/approvalPolicy 的映射；
 * - 桌面壳在启动运行时注入 DSH_DESKTOP_CODEX_CLI（发现的 CLI 路径）与
 *   CODEX_HOME（隔离状态目录，auth.json 链接回真实文件）。
 */

/** codex app-server 的 JSON-RPC 通道（newline-delimited JSON over stdio）。 */
export interface CodexChannel {
  request(method: string, params?: Record<string, unknown>): Promise<Record<string, unknown>>
  notify(method: string, params?: Record<string, unknown>): void
  respond(id: number | string, result: Record<string, unknown>): void
  onNotification(listener: (notification: { method: string; params: Record<string, unknown> }) => void): void
  close(): Promise<void>
  readonly exited: Promise<string>
}

// 图片会以内联 data URL 进入 JSONL 帧；给 base64 膨胀和多图输入留出空间。
const FRAME_LIMIT = 32 * 1024 * 1024
const DEFAULT_TURN_TIMEOUT_MS = 10 * 60 * 1000

export function codexSpawnSpec(cliPath: string, platform = process.platform): {
  command: string
  args: string[]
  shell: boolean
} {
  const extension = extname(cliPath).toLowerCase()
  const shell = platform === 'win32' && (extension === '.cmd' || extension === '.bat')
  return { command: cliPath, args: ['app-server'], shell }
}

export interface CodexModel {
  id: string
  name: string
  description?: string
  inputModalities: string[]
  reasoning?: {
    efforts: Array<{ id: string; name: string; description?: string }>
    defaultEffort?: string
  }
}

export interface CodexUserInputText {
  type: 'text'
  text: string
}

export interface CodexUserInputImage {
  type: 'image'
  url: string
  detail?: 'auto' | 'low' | 'high' | 'original'
}

export type CodexUserInput = CodexUserInputText | CodexUserInputImage

export function openCodexChannel(cliPath: string, cwd: string): CodexChannel {
  const spawnSpec = codexSpawnSpec(cliPath)
  const child: ChildProcess = spawn(spawnSpec.command, spawnSpec.args, {
    cwd,
    env: buildCodexEnv(),
    shell: spawnSpec.shell,
    stdio: ['pipe', 'pipe', 'pipe'],
  })
  let nextId = 1
  let closed = false
  let finished = false
  const pending = new Map<number | string, { resolve(result: Record<string, unknown>): void; reject(error: Error): void }>()
  const listeners: Array<(notification: { method: string; params: Record<string, unknown> }) => void> = []
  let buffer = ''
  const stderrTail: string[] = []

  const exited = new Promise<string>((resolve) => {
    const finish = (fallback: string) => {
      if (finished) return
      finished = true
      closed = true
      const reason = stderrTail.filter((line) => !line.startsWith('WARNING:')).at(-1) ?? fallback
      for (const waiter of pending.values()) waiter.reject(new Error(`Codex app-server 已退出：${reason}`))
      pending.clear()
      resolve(reason)
    }
    child.once('exit', () => finish('app-server exited'))
    child.once('error', () => finish('app-server failed to start'))
  })

  child.stdout?.setEncoding('utf8')
  child.stdout?.on('data', (chunk: string) => {
    buffer += chunk
    if (Buffer.byteLength(buffer, 'utf8') > FRAME_LIMIT) {
      buffer = ''
      for (const waiter of pending.values()) waiter.reject(new Error('Codex app-server 返回的数据帧超过 32 MB 限制'))
      pending.clear()
      if (!child.killed) child.kill('SIGTERM')
      return
    }
    let newline = buffer.indexOf('\n')
    while (newline !== -1) {
      const line = buffer.slice(0, newline).trim()
      buffer = buffer.slice(newline + 1)
      if (line.length > 0) handleLine(line)
      newline = buffer.indexOf('\n')
    }
  })
  child.stderr?.setEncoding('utf8')
  child.stderr?.on('data', (chunk: string) => {
    for (const line of chunk.split('\n')) {
      const trimmed = line.trim()
      if (trimmed.length > 0) stderrTail.push(trimmed.slice(0, 200))
    }
    while (stderrTail.length > 6) stderrTail.shift()
  })

  function handleLine(line: string): void {
    let message: unknown
    try { message = JSON.parse(line) } catch { return }
    if (typeof message !== 'object' || message === null) return
    const record = message as Record<string, unknown>
    if (record.method === undefined) {
      const waiter = pending.get(record.id as number | string)
      if (waiter === undefined) return
      pending.delete(record.id as number | string)
      if (record.error !== undefined) waiter.reject(new Error(String((record.error as { message?: string })?.message ?? 'Codex 请求失败')))
      else waiter.resolve((record.result ?? {}) as Record<string, unknown>)
      return
    }
    for (const listener of listeners) listener({ method: String(record.method), params: (record.params ?? {}) as Record<string, unknown> })
  }

  function write(message: Record<string, unknown>): void {
    if (closed || child.stdin === null || !child.stdin.writable) throw new Error('Codex app-server 已退出')
    child.stdin.write(`${JSON.stringify(message)}\n`)
  }

  return {
    request(method, params = {}) {
      if (closed) return Promise.reject(new Error('Codex app-server 已退出'))
      const id = nextId++
      return new Promise<Record<string, unknown>>((resolve, reject) => {
        pending.set(id, { resolve, reject })
        try { write({ jsonrpc: '2.0', id, method, params }) }
        catch (cause) { pending.delete(id); reject(cause as Error) }
      })
    },
    notify(method, params = {}) { write({ jsonrpc: '2.0', method, params }) },
    respond(id, result) { write({ jsonrpc: '2.0', id, result }) },
    onNotification(listener) { listeners.push(listener) },
    async close() {
      closed = true
      child.stdin?.end()
      if (!child.killed) child.kill('SIGTERM')
      await exited.catch(() => undefined)
    },
    exited,
  }
}

function buildCodexEnv(): Record<string, string> {
  const keep = ['HOME', 'PATH', 'LANG', 'LC_ALL', 'TZ', 'TMPDIR', 'USER', 'CODEX_HOME', 'DSH_DESKTOP_CODEX_CLI']
  const env: Record<string, string> = {}
  for (const name of keep) {
    const value = process.env[name]
    if (value !== undefined && value !== '') env[name] = value
  }
  if (env.PATH === undefined) env.PATH = '/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin'
  return env
}

/** 解析 Codex CLI 路径：桌面壳注入的优先，其次 PATH 与已知安装位置。 */
export function resolveCodexCli(): string | null {
  const candidates: string[] = []
  const injected = process.env.DSH_DESKTOP_CODEX_CLI
  if (injected !== undefined && injected !== '' && isAbsolute(injected)) candidates.push(injected)
  if (process.env.PATH !== undefined) {
    for (const dir of process.env.PATH.split(':')) {
      if (dir !== '') candidates.push(join(dir, 'codex'))
    }
  }
  const home = homedir()
  candidates.push(
    join(home, '.codex', 'plugins', '.plugin-appserver', 'codex'),
    join(home, '.cargo', 'bin', 'codex'),
    '/opt/homebrew/bin/codex',
    '/usr/local/bin/codex',
  )
  for (const candidate of candidates) {
    if (existsSync(candidate)) return candidate
  }
  return null
}

/** 从当前登录的 Codex app-server 读取模型目录，避免在桌面端维护过期模型白名单。 */
export async function listCodexModels(cwd = process.cwd()): Promise<CodexModel[]> {
  const cli = resolveCodexCli()
  if (cli === null) return []
  const channel = openCodexChannel(cli, cwd)
  try {
    await channel.request('initialize', { clientInfo: { name: 'deepseek-harness-desktop', version: '0.1' } })
    channel.notify('initialized')
    const response = await channel.request('model/list', { includeHidden: false })
    const data = Array.isArray(response.data) ? response.data : []
    return data.flatMap((item): CodexModel[] => {
      if (typeof item !== 'object' || item === null) return []
      const record = item as Record<string, unknown>
      const id = typeof record.model === 'string' && record.model !== ''
        ? record.model
        : typeof record.id === 'string' ? record.id : ''
      if (id === '' || record.hidden === true) return []
      const efforts = Array.isArray(record.supportedReasoningEfforts)
        ? record.supportedReasoningEfforts.flatMap((effort) => {
          if (typeof effort !== 'object' || effort === null) return []
          const effortRecord = effort as Record<string, unknown>
          const value = effortRecord.reasoningEffort
          if (typeof value !== 'string' || value === '') return []
          const labels: Record<string, string> = {
            none: '关闭',
            minimal: '轻度',
            low: '轻度',
            medium: '中',
            high: '高',
            xhigh: '极高',
            max: '最大',
            ultra: '极限',
          }
          return [{
            id: value,
            name: labels[value] ?? value,
            description: typeof effortRecord.description === 'string' ? effortRecord.description : undefined,
          }]
        })
        : []
      const reasoning = efforts.length > 0
        ? {
            efforts,
            ...(typeof record.defaultReasoningEffort === 'string' && record.defaultReasoningEffort !== ''
              ? { defaultEffort: record.defaultReasoningEffort }
              : {}),
          }
        : undefined
      return [{
        id,
        name: typeof record.displayName === 'string' && record.displayName !== '' ? record.displayName : id,
        description: typeof record.description === 'string' ? record.description : undefined,
        inputModalities: Array.isArray(record.inputModalities)
          ? record.inputModalities.filter((modality): modality is string => typeof modality === 'string')
          : ['text', 'image'],
        reasoning,
      }]
    })
  } finally {
    await channel.close().catch(() => undefined)
  }
}

/** 每个聊天会话复用一个 Codex 线程：sessionId → threadId。 */
const threadBySession = new Map<string, { threadId: string; hasCompletedTurn: boolean }>()

export interface CodexTurnResult {
  text: string
  threadId: string
}

/**
 * 在 Codex 上跑一轮对话：必要时建线程（同会话 resume），发 turn 并把
 * agentMessage 增量通过 onDelta 流出；turn 结束后返回完整回复。
 */
export async function runCodexTurn(options: {
  sessionId: string
  prompt: string
  input?: CodexUserInput[]
  currentPrompt?: string
  currentInput?: CodexUserInput[]
  cwd: string
  model?: string
  reasoningEffort?: string
  permission?: 'request-approval' | 'smart-approval' | 'full-access'
  timeoutMs?: number
  system?: string
  onDelta(text: string): void
  signal?: AbortSignal
}): Promise<CodexTurnResult> {
  const cli = resolveCodexCli()
  if (cli === null) throw new Error('没有找到 Codex CLI。请到 设置 → 模型与 Agent → Agents 完成安装，或重启应用后再试。')
  const channel = openCodexChannel(cli, options.cwd)
  let settled = false
  let text = ''
  let activeThreadId = ''
  let abortHandler: (() => void) | undefined
  let timeout: ReturnType<typeof setTimeout> | undefined
  const turnTimeoutMs = options.timeoutMs ?? DEFAULT_TURN_TIMEOUT_MS
  let cancellationMessage: string | undefined
  const turnDone = new Promise<void>((resolveTurn, rejectTurn) => {
    const rejectCancelled = (message: string) => {
      if (settled) return
      settled = true
      cancellationMessage = message
      if (activeThreadId !== '') void channel.request('turn/interrupt', { threadId: activeThreadId }).catch(() => undefined)
      void channel.close()
      rejectTurn(new Error(message))
    }
    const onAbort = () => rejectCancelled('Codex 已取消。')
    abortHandler = onAbort
    if (options.signal?.aborted === true) {
      rejectCancelled('Codex 已取消。')
      return
    }
    options.signal?.addEventListener('abort', onAbort, { once: true })
    if (!Number.isSafeInteger(turnTimeoutMs) || turnTimeoutMs < 1 || turnTimeoutMs > 3_600_000) {
        rejectTurn(new Error('Codex 超时参数无效。'))
        return
    }
    timeout = setTimeout(() => rejectCancelled('Codex 请求超时，请重试。'), turnTimeoutMs)
    channel.onNotification(({ method, params }) => {
      if (settled) return
      if (method === 'item/agentMessage/delta' && typeof params.delta === 'string') {
        text += params.delta
        options.onDelta(params.delta)
        return
      }
      if (method === 'turn/completed') {
        const status = (params.turn as { status?: unknown } | undefined)?.status
        settled = true
        if (status !== 'completed') rejectTurn(new Error(`Codex 执行未完成：${String(status ?? 'unknown')}`))
        else resolveTurn()
        return
      }
      if (method === 'error' && params.willRetry !== true) {
        settled = true
        rejectTurn(new Error('Codex 请求失败，请重试。'))
      }
    })
    channel.exited.then(() => {
      if (!settled) {
        settled = true
        rejectTurn(new Error('Codex 提前退出，请重试。'))
      }
    })
  })
  // setup 阶段也可能被 AbortSignal 取消；先挂一个 noop handler，避免
  // initialize/thread 请求尚未返回时产生未处理的 Promise rejection。
  void turnDone.catch(() => undefined)
  try {
    await channel.request('initialize', { clientInfo: { name: 'deepseek-harness-desktop', version: '0.1' } })
    channel.notify('initialized')
    const threadOptions = permissionToCodexThreadOptions(options.permission ?? 'request-approval')
    const existing = threadBySession.get(options.sessionId)
    let resumed = existing?.hasCompletedTurn === true
    let threadId = existing?.threadId ?? ''
    if (threadId === '') {
      const thread = await channel.request('thread/start', {
        cwd: options.cwd,
        ...threadOptions,
        ...(options.system === undefined || options.system === '' ? {} : { baseInstructions: options.system }),
        ...(options.model === undefined || options.model === '' ? {} : { model: options.model }),
      })
      threadId = typeof thread.threadId === 'string'
        ? thread.threadId
        : typeof (thread.thread as { id?: string } | undefined)?.id === 'string'
          ? (thread.thread as { id: string }).id
          : ''
      if (threadId === '') throw new Error('Codex 没有返回线程标识，请重试。')
    } else {
      // app-server 是按进程保存连接状态的；新连接必须先 resume，不能直接 turn/start。
      try {
        await channel.request('thread/resume', {
          threadId,
          cwd: options.cwd,
          ...threadOptions,
          ...(options.system === undefined || options.system === '' ? {} : { baseInstructions: options.system }),
          ...(options.model === undefined || options.model === '' ? {} : { model: options.model }),
        })
      } catch {
        // 线程可能已被 Codex 清理；此时新建线程，避免把历史 prompt 重复塞入旧线程。
        threadBySession.delete(options.sessionId)
        resumed = false
        const thread = await channel.request('thread/start', {
          cwd: options.cwd,
          ...threadOptions,
          ...(options.system === undefined || options.system === '' ? {} : { baseInstructions: options.system }),
          ...(options.model === undefined || options.model === '' ? {} : { model: options.model }),
        })
        threadId = typeof thread.threadId === 'string'
          ? thread.threadId
          : typeof (thread.thread as { id?: string } | undefined)?.id === 'string'
            ? (thread.thread as { id: string }).id
            : ''
        if (threadId === '') throw new Error('Codex 没有返回线程标识，请重试。')
      }
    }
    activeThreadId = threadId
    const turnInput = resumed ? options.currentInput : options.input
    const turnPrompt = resumed ? (options.currentPrompt ?? options.prompt) : options.prompt
    await channel.request('turn/start', {
      threadId,
      input: turnInput?.length === 0 || turnInput === undefined
        ? [{ type: 'text', text: turnPrompt }]
        : turnInput,
      ...(options.model === undefined || options.model === '' ? {} : { model: options.model }),
      ...(options.reasoningEffort === undefined || options.reasoningEffort === '' ? {} : { effort: options.reasoningEffort }),
    })
    await turnDone
    threadBySession.set(options.sessionId, { threadId, hasCompletedTurn: true })
    return { text, threadId }
  } catch (cause) {
    if (cancellationMessage !== undefined) {
      // 取消可能发生在 initialize/thread/start 尚未返回时；避免把通道关闭
      // 造成的“提前退出”覆盖成用户真正触发的取消/超时原因。
      await turnDone.catch(() => undefined)
      throw new Error(cancellationMessage)
    }
    throw cause
  } finally {
    if (abortHandler !== undefined) options.signal?.removeEventListener('abort', abortHandler)
    if (timeout !== undefined) clearTimeout(timeout)
    await channel.close().catch(() => undefined)
  }
}
