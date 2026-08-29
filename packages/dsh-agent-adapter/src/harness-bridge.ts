import { spawn as spawnProcess, type ChildProcessWithoutNullStreams, type SpawnOptions } from 'node:child_process'

export const HARNESS_HOST_SESSION_SCHEMA = 'harness-host-session.v1' as const
export const HARNESS_BRIDGE_MAX_BYTES = 256 * 1024

const identifier = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/
const sha256 = /^(?:[0-9a-f]{64})?$/
const sensitiveKey = /^(?:api[_-]?key|authorization|cookie|password|private[_-]?key|provider[_-]?payload|raw[_-]?payload|secret|set-cookie|token)$/i
const sensitiveText = /\b(?:basic|bearer)\s+\S+/i

export type HarnessHostMessageType = 'agent.request' | 'agent.result' | 'session.event' | 'task.start' | 'task.result' | 'session.cancel'

export interface HarnessHostMessage {
  schema_version: typeof HARNESS_HOST_SESSION_SCHEMA
  type: HarnessHostMessageType
  request_id: string
  payload: Record<string, unknown>
}

export interface HarnessAgentRequest {
  schema_version: 'his-agent-backend-request.v1'
  role: 'worker' | 'reviewer'
  worktree_path: string
  prompt: string
  timeout_seconds: number
  output_contract: { name: string; schema_version: string }
  capabilities: string[]
}

export interface HarnessAgentResult {
  schema_version: 'his-agent-backend-result.v1'
  exit_code: number | null
  error_code: string
  event_count: number
  final_response_sha256: string
  canonical_final_response_sha256: string
  final_response_validated: boolean
  final_response?: Record<string, unknown>
}

export interface HarnessTransport {
  send(message: HarnessHostMessage): void | Promise<void>
  onMessage(listener: (message: unknown) => void): () => void
  close?(): void | Promise<void>
}

export interface HarnessTaskStartPayload {
  schema_version: 'harness-external-task.v1'
  task_contract_path: string
  understanding_path: string
  worktree_root: string
  knowledge_home: string
  authorization_id: string
  agent_backend?: string
}

export interface HarnessTaskResult {
  status: 'accepted' | 'completed' | 'blocked' | 'failed'
  error_code: string
  understanding_sha256?: string
  snapshot?: Record<string, unknown>
}

export type HarnessAgentRequestListener = (request: HarnessAgentRequest, requestId: string) => void | Promise<void>

export interface HarnessProcessTransportOptions {
  command: string
  args?: readonly string[]
  cwd: string
  env?: NodeJS.ProcessEnv
  spawn?: (command: string, args: readonly string[], options: SpawnOptions) => ChildProcessWithoutNullStreams
}

/** Spawn the provider-neutral Python sidecar with fixed args and a redacted env. */
export function createHarnessProcessTransport(options: HarnessProcessTransportOptions): HarnessTransport & { readonly pid: number | undefined } {
  if (!isAbsolutePath(options?.command) || !isAbsolutePath(options?.cwd)) throw new Error('Harness Sidecar 参数无效')
  const args = [...(options.args ?? [])]
  if (args.some((value) => typeof value !== 'string' || value.length > 4096 || value.includes('\u0000'))) throw new Error('Harness Sidecar 参数无效')
  const spawn = options.spawn ?? spawnProcess
  const child = spawn(options.command, args, {
    cwd: options.cwd,
    env: buildHarnessSidecarEnv(options.env),
    shell: false,
    stdio: ['pipe', 'pipe', 'pipe'],
  })
  const listeners = new Set<(message: unknown) => void>()
  let buffer = ''
  let closed = false
  child.stdout.setEncoding('utf8')
  child.stdout.on('data', (chunk: string) => {
    buffer += chunk
    if (Buffer.byteLength(buffer, 'utf8') > HARNESS_BRIDGE_MAX_BYTES) {
      buffer = ''
      child.kill('SIGTERM')
      return
    }
    let newline = buffer.indexOf('\n')
    while (newline >= 0) {
      const line = buffer.slice(0, newline).replace(/\r$/, '')
      buffer = buffer.slice(newline + 1)
      if (line.trim() !== '') {
        try {
          for (const listener of listeners) listener(JSON.parse(line))
        } catch {
          for (const listener of listeners) listener(undefined)
        }
      }
      newline = buffer.indexOf('\n')
    }
  })
  child.on('error', () => { for (const listener of listeners) listener(undefined) })
  child.on('exit', () => { closed = true })

  return {
    pid: child.pid,
    send(message) {
      if (closed || !child.stdin.writable) throw new Error('Harness Sidecar 已退出')
      const validated = validateHostMessage(message)
      const serialized = JSON.stringify(validated)
      if (Buffer.byteLength(serialized, 'utf8') > HARNESS_BRIDGE_MAX_BYTES) throw new Error('Harness 消息超出限制')
      child.stdin.write(`${serialized}\n`)
    },
    onMessage(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
    async close() {
      if (closed) return
      closed = true
      child.stdin.end()
      if (!child.killed) child.kill('SIGTERM')
    },
  }
}

export class HarnessBridgeClient {
  private readonly pending = new Map<string, { resolve(value: HarnessAgentResult): void; reject(cause: Error): void; timer: ReturnType<typeof setTimeout> }>()
  private readonly pendingTasks = new Map<string, { resolve(value: HarnessTaskResult): void; reject(cause: Error): void; timer: ReturnType<typeof setTimeout> }>()
  private readonly eventListeners = new Set<(payload: Record<string, unknown>) => void>()
  private readonly agentRequestListeners = new Set<HarnessAgentRequestListener>()
  private readonly disposeTransport: () => void
  private disposed = false

  constructor(private readonly transport: HarnessTransport) {
    this.disposeTransport = transport.onMessage((message) => this.handleMessage(message))
  }

  awaitAgentResult(requestId: string, timeoutMs = 120_000): Promise<HarnessAgentResult> {
    if (!identifier.test(requestId) || !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 3_600_000) {
      return Promise.reject(new Error('Harness 请求参数无效'))
    }
    if (this.disposed) return Promise.reject(new Error('Harness 桥已关闭'))
    return new Promise<HarnessAgentResult>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(requestId)
        reject(new Error('Harness Agent 响应超时'))
      }, timeoutMs)
      this.pending.set(requestId, { resolve, reject, timer })
    })
  }

  awaitTaskResult(requestId: string, timeoutMs = 120_000): Promise<HarnessTaskResult> {
    if (!identifier.test(requestId) || !Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 3_600_000) {
      return Promise.reject(new Error('Harness 任务参数无效'))
    }
    if (this.disposed) return Promise.reject(new Error('Harness 桥已关闭'))
    return new Promise<HarnessTaskResult>((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pendingTasks.delete(requestId)
        reject(new Error('Harness 任务响应超时'))
      }, timeoutMs)
      this.pendingTasks.set(requestId, { resolve, reject, timer })
    })
  }

  sendAgentResult(requestId: string, result: HarnessAgentResult): void | Promise<void> {
    if (this.disposed) throw new Error('Harness 桥已关闭')
    if (!identifier.test(requestId)) throw new Error('Harness 请求关联失败')
    const payload = validateAgentResult(result)
    return this.transport.send({
      schema_version: HARNESS_HOST_SESSION_SCHEMA,
      type: 'agent.result',
      request_id: requestId,
      payload: { ...payload },
    })
  }

  startTask(payload: HarnessTaskStartPayload, requestId: string = crypto.randomUUID()): string {
    if (this.disposed) throw new Error('Harness 桥已关闭')
    if (!identifier.test(requestId)) throw new Error('Harness 任务参数无效')
    const validated = validateTaskStartPayload(payload)
    void this.transport.send({
      schema_version: HARNESS_HOST_SESSION_SCHEMA,
      type: 'task.start',
      request_id: requestId,
      payload: validated,
    })
    return requestId
  }

  cancelTask(requestId: string): void | Promise<void> {
    if (this.disposed) throw new Error('Harness 桥已关闭')
    if (!identifier.test(requestId)) throw new Error('Harness 任务参数无效')
    return this.transport.send({
      schema_version: HARNESS_HOST_SESSION_SCHEMA,
      type: 'session.cancel',
      request_id: requestId,
      payload: {},
    })
  }

  onEvent(listener: (payload: Record<string, unknown>) => void): () => void {
    if (typeof listener !== 'function') throw new TypeError('Harness 事件监听器无效')
    this.eventListeners.add(listener)
    return () => this.eventListeners.delete(listener)
  }

  onAgentRequest(listener: HarnessAgentRequestListener): () => void {
    if (typeof listener !== 'function') throw new TypeError('Harness Agent 请求监听器无效')
    this.agentRequestListeners.add(listener)
    return () => this.agentRequestListeners.delete(listener)
  }

  sendEvent(requestId: string, payload: Record<string, unknown>): void | Promise<void> {
    if (this.disposed) throw new Error('Harness 桥已关闭')
    if (!identifier.test(requestId) || !isRecord(payload) || containsSensitiveShape(payload)) throw new Error('Harness 事件无效')
    return this.transport.send({
      schema_version: HARNESS_HOST_SESSION_SCHEMA,
      type: 'session.event',
      request_id: requestId,
      payload,
    })
  }

  dispose(): void {
    if (this.disposed) return
    this.disposed = true
    this.disposeTransport()
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer)
      pending.reject(new Error('Harness 桥已关闭'))
    }
    this.pending.clear()
    for (const pending of this.pendingTasks.values()) {
      clearTimeout(pending.timer)
      pending.reject(new Error('Harness 桥已关闭'))
    }
    this.pendingTasks.clear()
    void this.transport.close?.()
  }

  private handleMessage(value: unknown): void {
    let message: HarnessHostMessage
    try {
      message = validateHostMessage(value)
    } catch {
      this.rejectPending(new Error('Harness 响应格式无效'))
      return
    }
    if (message.type === 'session.event') {
      for (const listener of this.eventListeners) listener(message.payload)
      return
    }
    if (message.type === 'agent.request') {
      let request: HarnessAgentRequest
      try {
        request = validateAgentRequestPayload(message.payload)
      } catch {
        this.rejectPending(new Error('Harness Agent 请求无效'))
        return
      }
      for (const listener of this.agentRequestListeners) void listener(request, message.request_id)
      return
    }
    if (message.type === 'task.result') {
      const pending = this.pendingTasks.get(message.request_id)
      if (pending === undefined) {
        this.rejectPending(new Error('Harness 任务关联失败'))
        return
      }
      this.pendingTasks.delete(message.request_id)
      clearTimeout(pending.timer)
      try {
        pending.resolve(validateTaskResult(message.payload))
      } catch {
        pending.reject(new Error('Harness 任务响应无效'))
      }
      return
    }
    if (message.type !== 'agent.result') return
    const pending = this.pending.get(message.request_id)
    if (pending === undefined) {
      this.rejectPending(new Error('Harness 请求关联失败'))
      return
    }
    this.pending.delete(message.request_id)
    clearTimeout(pending.timer)
    try {
      pending.resolve(validateAgentResult(message.payload))
    } catch {
      pending.reject(new Error('Harness Agent 响应无效'))
    }
  }

  private rejectPending(error: Error): void {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer)
      pending.reject(error)
    }
    this.pending.clear()
    for (const pending of this.pendingTasks.values()) {
      clearTimeout(pending.timer)
      pending.reject(error)
    }
    this.pendingTasks.clear()
  }
}

export function validateHostMessage(value: unknown): HarnessHostMessage {
  if (!isRecord(value)
    || !hasExactKeys(value, ['payload', 'request_id', 'schema_version', 'type'])
    || value.schema_version !== HARNESS_HOST_SESSION_SCHEMA
    || typeof value.type !== 'string'
    || !['agent.request', 'agent.result', 'session.event', 'task.start', 'task.result', 'session.cancel'].includes(value.type)
    || typeof value.request_id !== 'string'
    || !identifier.test(value.request_id)
    || !isRecord(value.payload)
    || containsSensitiveShape(value.payload)
    || byteSize(value) > HARNESS_BRIDGE_MAX_BYTES) {
    throw new Error('Harness 消息无效')
  }
  return value as unknown as HarnessHostMessage
}

function validateAgentResult(value: unknown): HarnessAgentResult {
  if (!isRecord(value)
    || value.schema_version !== 'his-agent-backend-result.v1'
    || !hasExactKeys(value, [
      'canonical_final_response_sha256',
      'error_code',
      'event_count',
      'exit_code',
      'final_response_sha256',
      'final_response_validated',
      'schema_version',
    ], ['final_response'])
    || (value.exit_code !== null && (!Number.isSafeInteger(value.exit_code) || (value.exit_code as number) < -255 || (value.exit_code as number) > 255))
    || typeof value.error_code !== 'string'
    || !/^[a-z0-9._-]{0,64}$/.test(value.error_code)
    || !Number.isSafeInteger(value.event_count)
    || (value.event_count as number) < 0
    || !sha256.test(String(value.final_response_sha256))
    || !sha256.test(String(value.canonical_final_response_sha256))
    || typeof value.final_response_validated !== 'boolean'
    || (value.final_response_validated && !isRecord(value.final_response))) {
    throw new Error('Harness Agent 响应无效')
  }
  return value as unknown as HarnessAgentResult
}

function validateTaskStartPayload(value: unknown): Record<string, unknown> {
  if (!isRecord(value)
    || value.schema_version !== 'harness-external-task.v1'
    || !hasExactKeys(value, [
      'authorization_id',
      'knowledge_home',
      'schema_version',
      'task_contract_path',
      'understanding_path',
      'worktree_root',
    ], ['agent_backend'])
    || !['task_contract_path', 'understanding_path', 'worktree_root', 'knowledge_home', 'authorization_id'].every((key) => typeof value[key] === 'string' && (value[key] as string).length > 0)
    || (value.agent_backend !== undefined && (typeof value.agent_backend !== 'string' || !/^[a-z][a-z0-9._-]{1,63}$/.test(value.agent_backend)))) {
    throw new Error('Harness 任务参数无效')
  }
  if (containsSensitiveShape(value)) throw new Error('Harness 任务参数无效')
  return value
}

function validateAgentRequestPayload(value: unknown): HarnessAgentRequest {
  if (!isRecord(value)
    || value.schema_version !== 'his-agent-backend-request.v1'
    || !['worker', 'reviewer'].includes(String(value.role))
    || typeof value.worktree_path !== 'string'
    || !isAbsolutePath(value.worktree_path)
    || typeof value.prompt !== 'string'
    || value.prompt.trim() === ''
    || Buffer.byteLength(value.prompt, 'utf8') > 48 * 1024
    || !Number.isSafeInteger(value.timeout_seconds)
    || (value.timeout_seconds as number) < 1
    || (value.timeout_seconds as number) > 3_600
    || !isRecord(value.output_contract)
    || typeof value.output_contract.name !== 'string'
    || typeof value.output_contract.schema_version !== 'string'
    || !Array.isArray(value.capabilities)
    || value.capabilities.length > 128
    || value.capabilities.some((item) => typeof item !== 'string' || !identifier.test(item))
    || containsSensitiveShape(value)) {
    throw new Error('Harness Agent 请求无效')
  }
  return value as unknown as HarnessAgentRequest
}

function validateTaskResult(value: unknown): HarnessTaskResult {
  if (!isRecord(value)
    || !['accepted', 'completed', 'blocked', 'failed'].includes(String(value.status))
    || typeof value.error_code !== 'string'
    || !/^[a-z0-9._-]{0,64}$/.test(value.error_code)
    || (value.understanding_sha256 !== undefined && !/^[0-9a-f]{64}$/.test(String(value.understanding_sha256)))
    || (value.snapshot !== undefined && !isRecord(value.snapshot))
    || containsSensitiveShape(value)) {
    throw new Error('Harness 任务响应无效')
  }
  return value as unknown as HarnessTaskResult
}

function containsSensitiveShape(value: unknown, key = ''): boolean {
  if (sensitiveKey.test(key)) return true
  if (typeof value === 'string') return sensitiveText.test(value)
  if (Array.isArray(value)) return value.some((item) => containsSensitiveShape(item))
  if (isRecord(value)) return Object.entries(value).some(([name, nested]) => containsSensitiveShape(nested, name))
  return false
}

function hasExactKeys(value: Record<string, unknown>, required: string[], optional: string[] = []): boolean {
  const allowed = new Set([...required, ...optional])
  return required.every((key) => Object.hasOwn(value, key)) && Object.keys(value).every((key) => allowed.has(key))
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function byteSize(value: unknown): number {
  try { return new TextEncoder().encode(JSON.stringify(value)).byteLength } catch { return Number.POSITIVE_INFINITY }
}

function buildHarnessSidecarEnv(input: NodeJS.ProcessEnv | undefined): NodeJS.ProcessEnv {
  const allowed = ['HOME', 'PATH', 'LANG', 'LC_ALL', 'TZ', 'TMPDIR', 'USER', 'HARNESS_DB_PATH']
  const output: NodeJS.ProcessEnv = {}
  for (const key of allowed) {
    const value = input?.[key] ?? process.env[key]
    if (value !== undefined && value !== '') output[key] = value
  }
  if (output.PATH === undefined) output.PATH = '/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin'
  return output
}

function isAbsolutePath(value: unknown): value is string {
  return typeof value === 'string' && value.startsWith('/') && value.length > 1 && !value.includes('\u0000')
}
