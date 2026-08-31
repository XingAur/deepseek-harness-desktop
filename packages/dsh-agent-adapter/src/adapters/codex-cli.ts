import { spawn, type ChildProcess } from 'node:child_process'
import type { PermissionProfile } from '../protocol.js'
import { AdapterProtocolError, asRecord, type AdapterSemanticEvent } from './contracts.js'

/**
 * Real Codex CLI adapter speaking the official `codex app-server` JSON-RPC
 * protocol (newline-delimited JSON over stdio). The protocol surface below was
 * extracted from `codex app-server generate-json-schema` (codex-cli 0.149.x).
 */

export const CODEX_APP_SERVER_CLIENT_NAME = 'deepseek-harness-desktop'

export type CodexSandboxMode = 'read-only' | 'workspace-write' | 'danger-full-access'
export type CodexApprovalPolicy = 'untrusted' | 'on-request' | 'never'
export type CodexApprovalDecision = 'accept' | 'acceptForSession' | 'decline' | 'cancel'

export interface CodexThreadOptions {
  sandbox: CodexSandboxMode
  approvalPolicy: CodexApprovalPolicy
}

export function permissionToCodexThreadOptions(permission: PermissionProfile): CodexThreadOptions {
  switch (permission) {
    case 'full-access':
      return { sandbox: 'danger-full-access', approvalPolicy: 'never' }
    case 'smart-approval':
      return { sandbox: 'workspace-write', approvalPolicy: 'on-request' }
    default:
      return { sandbox: 'workspace-write', approvalPolicy: 'untrusted' }
  }
}

export interface CodexServerRequest {
  id: number | string
  method: string
  params: Record<string, unknown>
}

export interface CodexNotification {
  method: string
  params: Record<string, unknown>
}

/** A duplex JSON-RPC connection to `codex app-server`. */
export interface CodexAppServerChannel {
  request(method: string, params?: Record<string, unknown>): Promise<Record<string, unknown>>
  notify(method: string, params?: Record<string, unknown>): void
  respond(serverRequest: CodexServerRequest, result: Record<string, unknown>): void
  close(): Promise<void>
  readonly exited: Promise<void>
}

export interface CodexAppServerChannelOptions {
  cliPath: string
  cwd: string
  env?: Record<string, string>
  onNotification(notification: CodexNotification): void
  onServerRequest(serverRequest: CodexServerRequest): void
  spawnProcess?(command: string, args: string[], options: { cwd: string; env: Record<string, string> }): ChildProcess
}

export function openCodexAppServerChannel(options: CodexAppServerChannelOptions): CodexAppServerChannel {
  const child = (options.spawnProcess ?? defaultSpawn)(
    options.cliPath,
    ['app-server'],
    { cwd: options.cwd, env: options.env ?? {} },
  )
  let nextRequestId = 1
  let closed = false
  const pending = new Map<number | string, { resolve(result: Record<string, unknown>): void; reject(error: Error): void }>()
  let buffer = ''
  let stdoutClosed = false
  const rejectPending = (message: string) => {
    for (const waiter of pending.values()) waiter.reject(new AdapterProtocolError('server-closed', message))
    pending.clear()
  }
  const exited = new Promise<void>((resolve) => {
    const finish = (fallback: string) => {
      closed = true
      const reason = lastStderrLine() ?? fallback
      rejectPending(`Codex app-server 已退出：${reason}`)
      resolve()
    }
    void child.once('exit', () => finish('app-server exited'))
    void child.once('error', () => finish('app-server failed to start'))
  })

  child.stdout?.setEncoding('utf8')
  child.stdout?.on('data', (chunk: string) => {
    if (stdoutClosed) return
    buffer += chunk
    // Guard unbounded buffering: a single oversized line is dropped with an error.
    if (buffer.length > 4 * 1024 * 1024) {
      buffer = ''
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
  child.stdout?.once('close', () => { stdoutClosed = true })
  // 保留 stderr 尾行（有界、脱敏路径）：app-server 启动失败时这是唯一的
  // 诊断来源，会进入 CODEX_START_FAILED 的人话提示。
  const stderrTail: string[] = []
  child.stderr?.setEncoding('utf8')
  child.stderr?.on('data', (chunk: string) => {
    for (const line of chunk.split('\n')) {
      const trimmed = line.trim()
      if (trimmed.length > 0) stderrTail.push(trimmed.slice(0, 200))
    }
    while (stderrTail.length > 6) stderrTail.shift()
  })

  function lastStderrLine(): string | undefined {
    const relevant = stderrTail.filter((line) => !line.startsWith('WARNING:'))
    return relevant.at(-1)
  }

  function handleLine(line: string): void {
    let message: unknown
    try {
      message = JSON.parse(line)
    } catch {
      return
    }
    if (typeof message !== 'object' || message === null) return
    const record = message as Record<string, unknown>
    if (record.method === undefined) {
      const waiter = pending.get(record.id as number | string)
      if (waiter === undefined) return
      pending.delete(record.id as number | string)
      if (record.error !== undefined) waiter.reject(new AdapterProtocolError('provider-error', safeText(record.error, 256) || 'Codex app-server request failed'))
      else waiter.resolve(asRecord(record.result ?? {}, 'Codex app-server result'))
      return
    }
    if (record.id !== undefined) {
      options.onServerRequest({ id: record.id as number | string, method: String(record.method), params: asRecord(record.params ?? {}, 'Codex app-server request params') })
      return
    }
    options.onNotification({ method: String(record.method), params: asRecord(record.params ?? {}, 'Codex app-server notification params') })
  }

  function write(message: Record<string, unknown>): void {
    if (closed) throw new AdapterProtocolError('server-closed', 'Codex app-server already exited')
    const payload = `${JSON.stringify(message)}\n`
    if (child.stdin === null || !child.stdin.writable) {
      throw new AdapterProtocolError('server-closed', 'Codex app-server stdin is closed')
    }
    child.stdin.write(payload)
  }

  return {
    request(method, params = {}) {
      if (closed) return Promise.reject(new AdapterProtocolError('server-closed', 'Codex app-server already exited'))
      const id = nextRequestId++
      return new Promise<Record<string, unknown>>((resolve, reject) => {
        pending.set(id, { resolve, reject })
        try {
          write({ jsonrpc: '2.0', id, method, params })
        } catch (cause) {
          pending.delete(id)
          reject(cause as Error)
        }
      })
    },
    notify(method, params = {}) {
      write({ jsonrpc: '2.0', method, params })
    },
    respond(serverRequest, result) {
      write({ jsonrpc: '2.0', id: serverRequest.id, result })
    },
    async close() {
      closed = true
      for (const waiter of pending.values()) {
        waiter.reject(new AdapterProtocolError('server-closed', 'Codex app-server closed'))
      }
      pending.clear()
      child.stdin?.end()
      if (!child.killed) child.kill('SIGTERM')
      await exited
    },
    exited,
  }
}

function defaultSpawn(command: string, args: string[], options: { cwd: string; env: Record<string, string> }): ChildProcess {
  return spawn(command, args, { cwd: options.cwd, env: options.env, stdio: ['pipe', 'pipe', 'pipe'] })
}

const APPROVAL_CAPABILITIES: Record<string, string> = {
  'item/commandExecution/requestApproval': 'terminal',
  'item/fileChange/requestApproval': 'file-write',
  'item/permissions/requestApproval': 'external-write',
  applyPatchApproval: 'file-write',
  execCommandApproval: 'terminal',
}

export interface CodexApprovalDescriptor {
  capability: string
  scope: string
}

export function approvalDescriptorFor(serverRequest: CodexServerRequest): CodexApprovalDescriptor {
  const capability = APPROVAL_CAPABILITIES[serverRequest.method] ?? 'process-launch'
  const params = serverRequest.params
  const command = typeof params.command === 'string' ? params.command : undefined
  const reason = typeof params.reason === 'string' ? params.reason : undefined
  const paths = Array.isArray(params.paths) ? params.paths.filter((value): value is string => typeof value === 'string') : []
  const scope = command !== undefined
    ? command.slice(0, 480)
    : paths.length > 0
      ? paths.slice(0, 4).join(', ').slice(0, 480)
      : reason !== undefined
        ? reason.slice(0, 480)
        : serverRequest.method
  return { capability, scope }
}

export function desktopDecisionToCodex(decision: 'allow-once' | 'allow-for-task' | 'deny'): CodexApprovalDecision {
  switch (decision) {
    case 'allow-for-task': return 'acceptForSession'
    case 'deny': return 'decline'
    default: return 'accept'
  }
}

/** Map one app-server notification to zero or more protocol events. */
export function mapCodexNotification(notification: CodexNotification): AdapterSemanticEvent[] {
  const params = notification.params
  switch (notification.method) {
    case 'thread/started':
    case 'turn/started':
      return []
    case 'item/agentMessage/delta': {
      const delta = typeof params.delta === 'string' ? params.delta : ''
      return splitDelta(delta).map((text) => ({ type: 'message.delta' as const, payload: { text } }))
    }
    case 'item/started': {
      const kind = itemType(params.item)
      if (kind === 'commandExecution') return [{ type: 'command.started' as const, payload: {} }]
      if (kind === 'mcpToolCall' || kind === 'dynamicToolCall') return [{ type: 'tool.started' as const, payload: {} }]
      return []
    }
    case 'item/completed': {
      const kind = itemType(params.item)
      const item = asRecord(params.item, 'item')
      if (kind === 'agentMessage') {
        const text = typeof item.text === 'string' ? item.text : ''
        return [
          ...splitDelta(text).map((chunk) => ({ type: 'message.delta' as const, payload: { text: chunk } })),
          { type: 'message.completed' as const, payload: { text: truncateBytes(text, 12 * 1024) } },
        ]
      }
      if (kind === 'commandExecution') return [{ type: 'command.completed' as const, payload: {} }]
      if (kind === 'mcpToolCall' || kind === 'dynamicToolCall') return [{ type: 'tool.completed' as const, payload: {} }]
      if (kind === 'fileChange') return [{ type: 'file.changed' as const, payload: {} }]
      return []
    }
    case 'item/fileChange/patchUpdated':
      return [{ type: 'file.changed' as const, payload: {} }]
    case 'turn/completed': {
      const turn = asRecord(params.turn ?? {}, 'turn')
      const status = typeof turn.status === 'string' ? turn.status : ''
      if (status === 'failed') return [{ type: 'session.failed' as const, payload: {} }]
      return [{ type: 'session.completed' as const, payload: {} }]
    }    case 'error': {
      const willRetry = params.willRetry === true
      if (willRetry) return []
      return [{ type: 'session.failed' as const, payload: {} }]
    }
    default:
      return []
  }
}

function itemType(value: unknown): string {
  if (typeof value !== 'object' || value === null) return ''
  const kind = (value as Record<string, unknown>).type
  return typeof kind === 'string' ? kind : ''
}

/** Split long text into bounded chunks so every protocol frame stays small. */
export function splitDelta(text: string, maximum = 4000): string[] {
  if (text.length === 0) return []
  const chunks: string[] = []
  let current = ''
  for (const character of text) {
    current += character
    if (Buffer.byteLength(current, 'utf8') >= maximum) {
      chunks.push(current)
      current = ''
    }
  }
  if (current.length > 0) chunks.push(current)
  return chunks
}

/** Truncate to a UTF-8 byte budget without splitting surrogate pairs. */
export function truncateBytes(text: string, maximumBytes: number): string {
  if (Buffer.byteLength(text, 'utf8') <= maximumBytes) return text
  let output = ''
  for (const character of text) {
    if (Buffer.byteLength(output + character, 'utf8') > maximumBytes) break
    output += character
  }
  return output
}

function safeText(value: unknown, maximum: number): string {
  if (typeof value !== 'string') return ''
  if (typeof value === 'object') return ''
  return value.slice(0, maximum)
}
