import { existsSync, lstatSync, realpathSync } from 'node:fs'
import { posix, win32 } from 'node:path'

export const MCP_PROTOCOL_VERSION = 'mcp/v1' as const
export const MCP_MAX_MESSAGE_BYTES = 32 * 1024
export const MCP_MAX_RESULT_BYTES = 16 * 1024

export type McpToolEffect = 'read' | 'write' | 'external'
export type McpPermissionDecision = 'allow' | 'approval-required' | 'deny'
export type McpErrorCode =
  | 'approval-required'
  | 'cancelled'
  | 'denied'
  | 'malformed-message'
  | 'output-limit'
  | 'protocol-mismatch'
  | 'remote-error'
  | 'server-crashed'
  | 'timeout'

export interface McpRequest {
  id: string
  method: string
  params: Record<string, unknown>
}

export interface McpTransport {
  connect(signal?: AbortSignal): Promise<void>
  request(request: McpRequest, signal?: AbortSignal): Promise<unknown>
  close(): Promise<void>
}

export interface McpToolDefinition {
  name: string
  description?: string
  effect: McpToolEffect
  inputSchema: unknown
}

export interface McpToolResultContent {
  type: 'text' | 'json'
  text?: string
  json?: unknown
}

export interface McpToolResult {
  content: McpToolResultContent[]
  isError?: boolean
}

export interface McpPermissionRule {
  serverId: string
  toolName: string
  effect: McpToolEffect
  taskId: string
  scope: string
}

export interface McpPermissionContext {
  taskId: string
  scope: string
}

export type McpPermissionEvaluator = (
  serverId: string,
  tool: McpToolDefinition,
  context: McpPermissionContext,
) => McpPermissionDecision

export interface McpClientOptions {
  timeoutMs?: number
  permission?: McpPermissionEvaluator
}

export class McpTransportError extends Error {
  readonly code: McpErrorCode

  constructor(code: McpErrorCode, message: string, _details?: unknown) {
    super(message)
    this.name = 'McpTransportError'
    this.code = code
  }
}

export function createMcpPermissionPolicy(rules: McpPermissionRule[]): McpPermissionEvaluator {
  return (serverId, tool, context) => rules.some((rule) => (
    rule.serverId === serverId
      && rule.toolName === tool.name
      && rule.effect === tool.effect
      && rule.taskId === context.taskId
      && scopeContains(rule.scope, context.scope)
  )) ? 'allow' : 'approval-required'
}

export class McpClient {
  private readonly timeoutMs: number
  private readonly permission: McpPermissionEvaluator
  private connected = false
  private nextId = 1
  private discoveredTools: McpToolDefinition[] = []

  constructor(
    private readonly serverId: string,
    private readonly transport: McpTransport,
    options: McpClientOptions = {},
  ) {
    this.timeoutMs = options.timeoutMs ?? 30_000
    this.permission = options.permission ?? (() => 'approval-required')
  }

  async connect(): Promise<void> {
    try {
      await this.connectTransport()
      const handshake = asRecord(await this.request('initialize', { protocolVersion: MCP_PROTOCOL_VERSION }))
      if (handshake.protocolVersion !== MCP_PROTOCOL_VERSION) {
        throw new McpTransportError('protocol-mismatch', 'MCP 协议版本不兼容')
      }
      const serverInfo = asRecord(handshake.serverInfo)
      if (typeof serverInfo.name !== 'string' || typeof serverInfo.version !== 'string') {
        throw new McpTransportError('malformed-message', 'MCP 服务端握手信息无效')
      }
      const listed = asRecord(await this.request('tools/list', {})).tools
      if (!Array.isArray(listed) || listed.length > 256) {
        throw new McpTransportError('malformed-message', 'MCP 工具列表无效')
      }
      this.discoveredTools = listed.map(parseTool)
      this.connected = true
    } catch (cause) {
      this.connected = false
      await this.transport.close().catch(() => {})
      throw cause
    }
  }

  private async connectTransport(): Promise<void> {
    const controller = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    let timedOut = false
    const timeoutError = new McpTransportError('timeout', 'MCP 连接超时')
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        timedOut = true
        controller.abort()
        reject(timeoutError)
      }, this.timeoutMs)
    })
    try {
      await Promise.race([this.transport.connect(controller.signal), timeout])
    } catch (cause) {
      if (timedOut) throw timeoutError
      throw cause
    } finally {
      if (timer !== undefined) clearTimeout(timer)
    }
  }

  tools(): readonly McpToolDefinition[] {
    return this.discoveredTools
  }

  async callTool(name: string, arguments_: Record<string, unknown>, context: McpPermissionContext & { signal?: AbortSignal }): Promise<McpToolResult> {
    if (!this.connected) throw new McpTransportError('server-crashed', 'MCP 服务尚未连接')
    const tool = this.discoveredTools.find((candidate) => candidate.name === name)
    if (tool === undefined) throw new McpTransportError('approval-required', '未登记的 MCP 工具必须先审核')
    const decision = this.permission(this.serverId, tool, context)
    if (decision !== 'allow') throw new McpTransportError(decision === 'deny' ? 'denied' : decision, decision === 'deny' ? 'MCP 工具调用被拒绝' : 'MCP 工具调用需要审批')
    assertJsonSize(arguments_, MCP_MAX_MESSAGE_BYTES, 'MCP 工具参数超限')
    const result = asRecord(await this.request('tools/call', { name, arguments: arguments_ }, context.signal))
    if (!Array.isArray(result.content)) throw new McpTransportError('malformed-message', 'MCP 工具结果无效')
    assertJsonSize(result, MCP_MAX_RESULT_BYTES, 'MCP 工具输出超限')
    return parseToolResult(result)
  }

  async close(): Promise<void> {
    this.connected = false
    await this.transport.close()
  }

  private async request(method: string, params: Record<string, unknown>, signal?: AbortSignal): Promise<unknown> {
    if (signal?.aborted) throw new McpTransportError('cancelled', 'MCP 请求已取消')
    const request: McpRequest = { id: `mcp-${this.nextId++}`, method, params }
    assertJsonSize(request, MCP_MAX_MESSAGE_BYTES, 'MCP 控制帧超限')
    const controller = new AbortController()
    const abortExternal = () => controller.abort()
    signal?.addEventListener('abort', abortExternal, { once: true })
    let timer: ReturnType<typeof setTimeout> | undefined
    let timedOut = false
    const timeoutError = new McpTransportError('timeout', 'MCP 请求超时')
    const timeout = new Promise<never>((_, reject) => {
      timer = setTimeout(() => {
        timedOut = true
        controller.abort()
        reject(timeoutError)
      }, this.timeoutMs)
    })
    try {
      return await Promise.race([this.transport.request(request, controller.signal), timeout])
    } catch (cause) {
      if (timedOut) throw timeoutError
      if (cause instanceof McpTransportError) throw cause
      if (signal?.aborted) throw new McpTransportError('cancelled', 'MCP 请求已取消')
      throw new McpTransportError('remote-error', 'MCP 服务端请求失败')
    } finally {
      if (timer !== undefined) clearTimeout(timer)
      signal?.removeEventListener('abort', abortExternal)
    }
  }
}

function parseTool(value: unknown): McpToolDefinition {
  const record = asRecord(value)
  if (!hasOnlyKeys(record, ['name', 'description', 'effect', 'inputSchema'])
    || typeof record.name !== 'string'
    || record.name.length === 0
    || record.name.length > 128
    || !isToolEffect(record.effect)
    || !Object.hasOwn(record, 'inputSchema')) {
    throw new McpTransportError('malformed-message', 'MCP 工具声明无效')
  }
  if (record.description !== undefined && (typeof record.description !== 'string' || record.description.length > 1024)) {
    throw new McpTransportError('malformed-message', 'MCP 工具描述无效')
  }
  return {
    name: record.name,
    ...(record.description === undefined ? {} : { description: record.description }),
    effect: record.effect,
    inputSchema: record.inputSchema,
  }
}

function parseToolResult(value: Record<string, unknown>): McpToolResult {
  if (!hasOnlyKeys(value, ['content', 'isError'])) throw new McpTransportError('malformed-message', 'MCP 工具结果包含未知字段')
  if (value.isError !== undefined && typeof value.isError !== 'boolean') throw new McpTransportError('malformed-message', 'MCP 工具错误标记无效')
  const content = (value.content as unknown[]).map((entry) => {
    const item = asRecord(entry)
    if (!hasOnlyKeys(item, ['type', 'text', 'json']) || (item.type !== 'text' && item.type !== 'json')) {
      throw new McpTransportError('malformed-message', 'MCP 工具内容无效')
    }
    if (item.type === 'text' && (typeof item.text !== 'string' || item.text.length > MCP_MAX_RESULT_BYTES)) {
      throw new McpTransportError('output-limit', 'MCP 工具文本输出超限')
    }
    if (item.type === 'json' && !Object.hasOwn(item, 'json')) throw new McpTransportError('malformed-message', 'MCP 工具 JSON 内容无效')
    return {
      type: item.type,
      ...(item.text === undefined ? {} : { text: item.text }),
      ...(item.json === undefined ? {} : { json: item.json }),
    } as McpToolResultContent
  })
  return { content, ...(value.isError === undefined ? {} : { isError: value.isError }) }
}

function scopeContains(granted: string, requested: string): boolean {
  if (granted === requested) return true
  const normalizedGranted = normalizePathScope(granted)
  const normalizedRequested = normalizePathScope(requested)
  if (normalizedGranted === null || normalizedRequested === null) return false
  if (!lexicalScopeContains(normalizedGranted, normalizedRequested)) return false
  const canonicalGranted = resolveExistingPath(granted)
  const canonicalRequested = resolveExistingPath(requested)
  if (canonicalGranted !== null && canonicalRequested !== null) {
    return lexicalScopeContains(canonicalGranted, canonicalRequested)
  }
  return false
}

function lexicalScopeContains(granted: string, requested: string): boolean {
  if (pathKey(granted) === pathKey(requested)) return true
  if (pathKey(granted) === pathKey('/')) return true
  const boundary = granted.endsWith('/') ? granted : `${granted}/`
  return pathKey(requested).startsWith(pathKey(boundary))
}

function resolveExistingPath(value: string): string | null {
  const normalized = normalizePathScope(value)
  if (normalized === null) return null
  const windowsStyle = /^[A-Za-z]:\//.test(normalized) || normalized.startsWith('//')
  const pathApi = windowsStyle ? win32 : posix
  let candidate = normalized
  const suffix: string[] = []
  while (!existsSync(nativePath(candidate))) {
    if (isSymbolicLink(nativePath(candidate))) return null
    const parent = pathApi.dirname(candidate)
    if (parent === candidate) return null
    suffix.unshift(pathApi.basename(candidate))
    candidate = parent
  }
  try {
    const canonical = realpathSync.native(nativePath(candidate)).replaceAll('\\', '/')
    return normalizePathScope(`${canonical}${suffix.length === 0 ? '' : `/${suffix.join('/')}`}`)
  } catch {
    return null
  }
}

function isSymbolicLink(value: string): boolean {
  try { return lstatSync(value).isSymbolicLink() } catch { return false }
}

function nativePath(value: string): string {
  return process.platform === 'win32' ? value.replaceAll('/', '\\') : value
}

function pathKey(value: string): string {
  return process.platform === 'win32' ? value.toLowerCase() : value
}

function normalizePathScope(value: string): string | null {
  const normalizedSeparators = value.replaceAll('\\', '/')
  const windowsDrive = normalizedSeparators.match(/^([A-Za-z]:)\//)
  const isUnc = normalizedSeparators.startsWith('//')
  const isPosix = normalizedSeparators.startsWith('/') && !isUnc
  if (!windowsDrive && !isUnc && !isPosix) return null

  const prefix = windowsDrive ? `${windowsDrive[1]}/` : isUnc ? '//' : '/'
  const rest = normalizedSeparators.slice(prefix.length)
  const parts: string[] = []
  for (const part of rest.split('/')) {
    if (part === '' || part === '.') continue
    if (part === '..') {
      if (parts.length === 0) return null
      parts.pop()
      continue
    }
    parts.push(part)
  }
  return prefix + parts.join('/')
}

function assertJsonSize(value: unknown, limit: number, message: string): void {
  let size: number
  try { size = new TextEncoder().encode(JSON.stringify(value)).byteLength } catch { size = Number.POSITIVE_INFINITY }
  if (size > limit) throw new McpTransportError('output-limit', message)
}

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new McpTransportError('malformed-message', 'MCP 消息不是对象')
  return value as Record<string, unknown>
}

function hasOnlyKeys(value: Record<string, unknown>, allowed: string[]): boolean {
  const keys = new Set(allowed)
  return Object.keys(value).every((key) => keys.has(key))
}

function isToolEffect(value: unknown): value is McpToolEffect {
  return value === 'read' || value === 'write' || value === 'external'
}

export {
  createMcpHttpTransport,
  createMcpSseTransport,
  createMcpStdioTransport,
  validateMcpEndpoint,
} from './transports.js'
export type { McpHttpTransportOptions, McpSseChannel, McpStdioProcess } from './transports.js'
