import {
  MCP_MAX_MESSAGE_BYTES,
  McpTransportError,
  type McpRequest,
  type McpTransport,
} from './client.js'

const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]'])

export interface McpStdioProcess {
  start(signal?: AbortSignal): Promise<void>
  write(line: string, signal?: AbortSignal): Promise<void>
  readLine(signal?: AbortSignal): Promise<string | null>
  kill(): Promise<void>
}

export interface McpSseChannel {
  connect(onMessage: (message: string) => void, signal?: AbortSignal): Promise<void>
  send(message: string, signal?: AbortSignal): Promise<void>
  close(): Promise<void>
}

export interface McpHttpTransportOptions {
  fetchImpl?: typeof fetch
  headers?: Record<string, string>
}

export function createMcpStdioTransport(process: McpStdioProcess): McpTransport {
  let connected = false
  return {
    async connect(signal) {
      ensureNotAborted(signal)
      await process.start(signal)
      ensureNotAborted(signal)
      connected = true
    },
    async request(request, signal) {
      ensureConnected(connected)
      ensureNotAborted(signal)
      const frame = JSON.stringify({ jsonrpc: '2.0', id: request.id, method: request.method, params: request.params })
      assertFrameSize(frame)
      try {
        await process.write(`${frame}\n`, signal)
        const line = await process.readLine(signal)
        if (line === null) throw new McpTransportError('server-crashed', 'MCP stdio 服务已退出')
        return parseResponse(line, request.id)
      } catch (cause) {
        throw normalizeTransportError(cause, signal, 'MCP stdio 请求失败')
      }
    },
    async close() {
      connected = false
      await process.kill()
    },
  }
}

export function createMcpHttpTransport(endpoint: string, options: McpHttpTransportOptions = {}): McpTransport {
  const normalizedEndpoint = validateMcpEndpoint(endpoint)
  const fetchImpl = options.fetchImpl ?? fetch
  let connected = false
  return {
    async connect(signal) {
      ensureNotAborted(signal)
      connected = true
    },
    async request(request, signal) {
      ensureConnected(connected)
      ensureNotAborted(signal)
      const frame = JSON.stringify({ jsonrpc: '2.0', id: request.id, method: request.method, params: request.params })
      assertFrameSize(frame)
      let response: Response
      try {
        response = await fetchImpl(normalizedEndpoint, {
          method: 'POST',
          redirect: 'error',
          signal,
          headers: { 'content-type': 'application/json', accept: 'application/json', ...options.headers },
          body: frame,
        })
      } catch (cause) {
        throw normalizeTransportError(cause, signal, 'MCP HTTP 请求失败')
      }
      if (!response.ok) throw new McpTransportError('remote-error', `MCP HTTP 请求失败（${response.status}）`)
      const body = await readBoundedResponse(response)
      return parseResponse(body, request.id)
    },
    async close() {
      connected = false
    },
  }
}

export function createMcpSseTransport(channel: McpSseChannel): McpTransport {
  let connected = false
  const pending = new Map<string, { resolve: (value: unknown) => void; reject: (error: unknown) => void }>()
  const onMessage = (message: string) => {
    let response: Record<string, unknown>
    try {
      response = asRecord(JSON.parse(message))
    } catch {
      return rejectAllPending(new McpTransportError('malformed-message', 'MCP SSE 消息无效'))
    }
    const id = response.id
    if (typeof id !== 'string') return
    const request = pending.get(id)
    if (request === undefined) return
    pending.delete(id)
    try {
      request.resolve(parseWireResponse(response, id))
    } catch (error) {
      request.reject(error)
    }
  }
  return {
    async connect(signal) {
      ensureNotAborted(signal)
      await channel.connect(onMessage, signal)
      ensureNotAborted(signal)
      connected = true
    },
    async request(request, signal) {
      ensureConnected(connected)
      ensureNotAborted(signal)
      const frame = JSON.stringify({ jsonrpc: '2.0', id: request.id, method: request.method, params: request.params })
      assertFrameSize(frame)
      return new Promise((resolve, reject) => {
        const abort = () => {
          pending.delete(request.id)
          reject(new McpTransportError('cancelled', 'MCP SSE 请求已取消'))
        }
        pending.set(request.id, { resolve, reject })
        signal?.addEventListener('abort', abort, { once: true })
        void channel.send(frame, signal).catch((cause) => {
          pending.delete(request.id)
          reject(normalizeTransportError(cause, signal, 'MCP SSE 请求失败'))
        })
      })
    },
    async close() {
      connected = false
      rejectAllPending(new McpTransportError('server-crashed', 'MCP SSE 服务已关闭'))
      await channel.close()
    },
  }

  function rejectAllPending(error: McpTransportError) {
    for (const request of pending.values()) request.reject(error)
    pending.clear()
  }
}

export function validateMcpEndpoint(value: string): string {
  let url: URL
  try {
    url = new URL(value)
  } catch {
    throw new McpTransportError('remote-error', 'MCP 服务地址无效')
  }
  const loopback = LOOPBACK_HOSTS.has(url.hostname)
  if (url.username !== '' || url.password !== '' || url.hash !== ''
    || (url.protocol !== 'https:' && !(loopback && url.protocol === 'http:'))) {
    throw new McpTransportError('remote-error', 'MCP 远程地址必须使用 HTTPS；HTTP 仅允许回环地址')
  }
  return url.toString()
}

function parseResponse(line: string, requestId: string): unknown {
  if (new TextEncoder().encode(line).byteLength > MCP_MAX_MESSAGE_BYTES) {
    throw new McpTransportError('output-limit', 'MCP 响应超限')
  }
  let value: unknown
  try {
    value = JSON.parse(line)
  } catch {
    throw new McpTransportError('malformed-message', 'MCP JSONL 响应无效')
  }
  return parseWireResponse(asRecord(value), requestId)
}

function parseWireResponse(response: Record<string, unknown>, requestId: string): unknown {
  if (response.jsonrpc !== '2.0' || response.id !== requestId) {
    throw new McpTransportError('malformed-message', 'MCP 响应关联信息无效')
  }
  if (Object.hasOwn(response, 'error')) throw new McpTransportError('remote-error', 'MCP 服务端返回错误')
  if (!Object.hasOwn(response, 'result')) throw new McpTransportError('malformed-message', 'MCP 响应缺少结果')
  return response.result
}

async function readBoundedResponse(response: Response): Promise<string> {
  const declaredLength = Number(response.headers.get('content-length') ?? '')
  if (Number.isFinite(declaredLength) && declaredLength > MCP_MAX_MESSAGE_BYTES) {
    throw new McpTransportError('output-limit', 'MCP HTTP 响应超限')
  }
  if (response.body === null || typeof response.body.getReader !== 'function') {
    const text = await response.text()
    if (new TextEncoder().encode(text).byteLength > MCP_MAX_MESSAGE_BYTES) throw new McpTransportError('output-limit', 'MCP HTTP 响应超限')
    return text
  }
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let total = 0
  let text = ''
  while (true) {
    const chunk = await reader.read()
    if (chunk.done) return text + decoder.decode()
    total += chunk.value.byteLength
    if (total > MCP_MAX_MESSAGE_BYTES) {
      await reader.cancel()
      throw new McpTransportError('output-limit', 'MCP HTTP 响应超限')
    }
    text += decoder.decode(chunk.value, { stream: true })
  }
}

function ensureConnected(connected: boolean): void {
  if (!connected) throw new McpTransportError('server-crashed', 'MCP 服务尚未连接')
}

function ensureNotAborted(signal?: AbortSignal): void {
  if (signal?.aborted) throw new McpTransportError('cancelled', 'MCP 请求已取消')
}

function assertFrameSize(frame: string): void {
  if (new TextEncoder().encode(frame).byteLength > MCP_MAX_MESSAGE_BYTES) throw new McpTransportError('output-limit', 'MCP 请求超限')
}

function normalizeTransportError(cause: unknown, signal: AbortSignal | undefined, message: string): McpTransportError {
  if (cause instanceof McpTransportError) return cause
  if (signal?.aborted) return new McpTransportError('cancelled', 'MCP 请求已取消')
  return new McpTransportError('remote-error', message)
}

function asRecord(value: unknown): Record<string, unknown> {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) throw new McpTransportError('malformed-message', 'MCP 消息不是对象')
  return value as Record<string, unknown>
}
