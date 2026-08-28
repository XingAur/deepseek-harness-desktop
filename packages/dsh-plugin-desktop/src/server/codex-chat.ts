import { spawn, type ChildProcess } from 'node:child_process'
import { existsSync } from 'node:fs'
import { homedir } from 'node:os'
import { extname, isAbsolute, join } from 'node:path'

/**
 * Codex 聊天模型适配器：把官方 Codex CLI（app-server JSON-RPC over stdio）
 * 注册为 DeepSeek Harness 的一个 LLM 提供方路由（provider: codex）。
 *
 * 注册后，聊天界面的模型选择器会自动出现 Codex；每个会话映射到一个
 * Codex 线程（按 GenerateOptions.sessionId 记忆并 resume），回复以
 * text-delta 流式呈现。
 *
 * v1 边界（如实告知用户）：
 * - Codex 在自己的沙箱内自治执行（workspace-write + 自动放行），文件与
 *   命令操作走 Codex 自身的沙箱策略，不经桌面端审批 UI；
 * - 桌面壳在启动运行时注入 DSH_DESKTOP_CODEX_CLI（发现的 CLI 路径）与
 *   CODEX_HOME（隔离状态目录，auth.json 链接回真实文件）。
 */

/** codex app-server 的 JSON-RPC 通道（newline-delimited JSON over stdio）。 */
export interface CodexChannel {
  request(method: string, params?: Record<string, unknown>): Promise<Record<string, unknown>>
  respond(id: number | string, result: Record<string, unknown>): void
  onNotification(listener: (notification: { method: string; params: Record<string, unknown> }) => void): void
  close(): Promise<void>
  readonly exited: Promise<string>
}

const FRAME_LIMIT = 4 * 1024 * 1024

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
  reasoningEfforts: string[]
  defaultReasoningEffort?: string
}

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
  const pending = new Map<number | string, { resolve(result: Record<string, unknown>): void; reject(error: Error): void }>()
  const listeners: Array<(notification: { method: string; params: Record<string, unknown> }) => void> = []
  let buffer = ''
  const stderrTail: string[] = []

  const exited = new Promise<string>((resolve) => {
    const finish = (fallback: string) => {
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
    if (buffer.length > FRAME_LIMIT) { buffer = ''; return }
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
    const response = await channel.request('model/list', { includeHidden: false })
    const data = Array.isArray(response.data) ? response.data : []
    return data.flatMap((item): CodexModel[] => {
      if (typeof item !== 'object' || item === null) return []
      const record = item as Record<string, unknown>
      const id = typeof record.model === 'string' && record.model !== ''
        ? record.model
        : typeof record.id === 'string' ? record.id : ''
      if (id === '' || record.hidden === true) return []
      const options = Array.isArray(record.supportedReasoningEfforts)
        ? record.supportedReasoningEfforts.flatMap((effort) => {
          if (typeof effort !== 'object' || effort === null) return []
          const value = (effort as Record<string, unknown>).reasoningEffort
          return typeof value === 'string' && value !== '' ? [value] : []
        })
        : []
      return [{
        id,
        name: typeof record.displayName === 'string' && record.displayName !== '' ? record.displayName : id,
        description: typeof record.description === 'string' ? record.description : undefined,
        reasoningEfforts: options,
        defaultReasoningEffort: typeof record.defaultReasoningEffort === 'string' ? record.defaultReasoningEffort : undefined,
      }]
    })
  } finally {
    await channel.close().catch(() => undefined)
  }
}

/** 每个聊天会话复用一个 Codex 线程：sessionId → threadId。 */
const threadBySession = new Map<string, string>()

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
  cwd: string
  model?: string
  reasoningEffort?: string
  system?: string
  onDelta(text: string): void
  signal?: AbortSignal
}): Promise<CodexTurnResult> {
  const cli = resolveCodexCli()
  if (cli === null) throw new Error('没有找到 Codex CLI。请到 设置 → 模型与 Agent → Agents 完成安装，或重启应用后再试。')
  const channel = openCodexChannel(cli, options.cwd)
  let settled = false
  let text = ''
  const turnDone = new Promise<void>((resolveTurn, rejectTurn) => {
    channel.onNotification(({ method, params }) => {
      if (method === 'item/agentMessage/delta' && typeof params.delta === 'string') {
        text += params.delta
        options.onDelta(params.delta)
        return
      }
      if (method === 'turn/completed') {
        settled = true
        resolveTurn()
        return
      }
      if (method === 'error' && params.willRetry !== true) {
        settled = true
        rejectTurn(new Error('Codex 请求失败，请重试。'))
      }
    })
    channel.exited.then(() => {
      if (!settled) rejectTurn(new Error('Codex 提前退出，请重试。'))
    })
  })
  try {
    await channel.request('initialize', { clientInfo: { name: 'deepseek-harness-desktop', version: '0.1' } })
    const existing = threadBySession.get(options.sessionId)
    let threadId = typeof existing === 'string' ? existing : ''
    if (threadId === '') {
      const thread = await channel.request('thread/start', {
        cwd: options.cwd,
        sandbox: 'workspace-write',
        approvalPolicy: 'never',
        ...(options.system === undefined || options.system === '' ? {} : { baseInstructions: options.system }),
        ...(options.model === undefined || options.model === '' ? {} : { model: options.model }),
      })
      threadId = typeof thread.threadId === 'string'
        ? thread.threadId
        : typeof (thread.thread as { id?: string } | undefined)?.id === 'string'
          ? (thread.thread as { id: string }).id
          : ''
      if (threadId === '') throw new Error('Codex 没有返回线程标识，请重试。')
      threadBySession.set(options.sessionId, threadId)
    } else {
      // app-server 是按进程保存连接状态的；新连接必须先 resume，不能直接 turn/start。
      await channel.request('thread/resume', {
        threadId,
        cwd: options.cwd,
        sandbox: 'workspace-write',
        approvalPolicy: 'never',
        ...(options.system === undefined || options.system === '' ? {} : { baseInstructions: options.system }),
        ...(options.model === undefined || options.model === '' ? {} : { model: options.model }),
      })
    }
    await channel.request('turn/start', {
      threadId,
      input: [{ type: 'text', text: options.prompt }],
      ...(options.model === undefined || options.model === '' ? {} : { model: options.model }),
      ...(options.reasoningEffort === undefined || options.reasoningEffort === '' ? {} : { effort: options.reasoningEffort }),
    })
    await turnDone
    return { text, threadId }
  } finally {
    await channel.close().catch(() => undefined)
  }
}
