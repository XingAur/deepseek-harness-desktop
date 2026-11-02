/** Bounded MCP handshakes used by the settings connection test. */

import { spawn } from 'node:child_process'
import { resolvedMcpArgs, type OverlayMcpServer } from './mcp-state.ts'

export interface McpProbeResult {
  readonly ok: boolean
  readonly toolCount?: number
  readonly serverInfoName?: string
  readonly serverInfoVersion?: string
  readonly error?: 'timeout' | 'spawn' | 'connect' | 'protocol' | 'http-status'
  readonly detail?: string
}

const PROTOCOL = '2024-11-05'
const CLIENT = { name: 'dsh-overlay-probe', version: '0.1.0' }

interface RpcMessage { readonly id?: unknown; readonly result?: unknown; readonly error?: unknown }

function parsePayload(raw: string): RpcMessage | undefined {
  const text = raw.trim()
  if (!text.startsWith('{')) return undefined
  try {
    const value: unknown = JSON.parse(text)
    return value !== null && typeof value === 'object' ? value as RpcMessage : undefined
  } catch {
    return undefined
  }
}

function parseLines(raw: string): RpcMessage[] {
  return raw.split('\n').map(parsePayload).filter((value): value is RpcMessage => value !== undefined)
}

function sseData(raw: string): string {
  return raw.split('\n').filter(line => line.startsWith('data:')).map(line => line.slice(5).trim()).join('\n')
}

function request(id: number, method: string, params?: Record<string, unknown>): string {
  return `${JSON.stringify({ jsonrpc: '2.0', id, method, ...(params === undefined ? {} : { params }) })}\n`
}

function failure(value: RpcMessage | undefined): McpProbeResult {
  if (value === undefined) return { ok: false, error: 'protocol', detail: 'server closed before answering' }
  if (value.error !== undefined) return { ok: false, error: 'protocol', detail: 'server rejected the MCP handshake' }
  return { ok: false, error: 'protocol', detail: 'unrecognized MCP response' }
}

function info(result: unknown): Pick<McpProbeResult, 'serverInfoName' | 'serverInfoVersion'> {
  if (result === null || typeof result !== 'object') return {}
  const serverInfo = (result as { serverInfo?: unknown }).serverInfo
  if (serverInfo === null || typeof serverInfo !== 'object') return {}
  const name = (serverInfo as { name?: unknown }).name
  const version = (serverInfo as { version?: unknown }).version
  return {
    ...(typeof name === 'string' && name.length > 0 ? { serverInfoName: name.slice(0, 120) } : {}),
    ...(typeof version === 'string' && version.length > 0 ? { serverInfoVersion: version.slice(0, 60) } : {}),
  }
}

function toolCount(result: unknown): number | undefined {
  if (result === null || typeof result !== 'object') return undefined
  const tools = (result as { tools?: unknown }).tools
  return Array.isArray(tools) ? tools.length : undefined
}

async function stdio(server: OverlayMcpServer, timeoutMs: number): Promise<McpProbeResult> {
  return await new Promise(resolve => {
    let child: ReturnType<typeof spawn>
    try {
      child = spawn(server.command!, resolvedMcpArgs(server), {
        ...(server.cwd === undefined ? {} : { cwd: server.cwd }),
        env: { ...process.env, ...server.env },
        stdio: ['pipe', 'pipe', 'pipe'],
      })
    } catch {
      resolve({ ok: false, error: 'spawn', detail: 'command could not be started' })
      return
    }
    let output = ''
    let initialized = false
    let settled = false
    let exitGrace: ReturnType<typeof setTimeout> | undefined
    const finish = (value: McpProbeResult): void => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      if (exitGrace !== undefined) clearTimeout(exitGrace)
      child.stdout?.removeAllListeners()
      child.removeAllListeners()
      try { child.kill('SIGKILL') } catch { /* process already exited */ }
      resolve(value)
    }
    const evaluate = (): void => {
      const messages = parseLines(output)
      const hello = messages.find(message => message.id === 1)
      if (hello === undefined) return
      if (hello.result === undefined) return finish(failure(hello))
      if (!initialized) {
        initialized = true
        try {
          child.stdin?.write(`${JSON.stringify({ jsonrpc: '2.0', method: 'notifications/initialized' })}\n`)
          child.stdin?.write(request(2, 'tools/list'))
        } catch { /* initialize result remains useful */ }
        return
      }
      const listed = messages.find(message => message.id === 2)
      if (listed === undefined) return
      const count = toolCount(listed.result)
      finish({ ok: true, ...info(hello.result), ...(count === undefined ? {} : { toolCount: count }) })
    }
    const timer = setTimeout(() => finish({ ok: false, error: 'timeout', detail: 'no MCP answer within the timeout' }), timeoutMs)
    child.on('error', () => finish({ ok: false, error: 'spawn', detail: 'command could not be started' }))
    child.on('exit', () => {
      exitGrace = setTimeout(() => {
        evaluate()
        finish({ ok: false, error: 'protocol', detail: 'server exited before completing the handshake' })
      }, 300)
    })
    child.stdout?.on('data', (chunk: Buffer) => { output += chunk.toString('utf8'); evaluate() })
    try {
      child.stdin?.write(request(1, 'initialize', { protocolVersion: PROTOCOL, capabilities: {}, clientInfo: CLIENT }))
    } catch {
      finish({ ok: false, error: 'spawn', detail: 'command stdin closed immediately' })
    }
  })
}

async function http(server: OverlayMcpServer, timeoutMs: number): Promise<McpProbeResult> {
  const abort = new AbortController()
  const timer = setTimeout(() => abort.abort(), timeoutMs)
  const headers = { 'content-type': 'application/json', accept: 'application/json, text/event-stream', ...server.headers }
  try {
    const helloResponse = await fetch(server.url!, {
      method: 'POST', headers,
      body: request(1, 'initialize', { protocolVersion: PROTOCOL, capabilities: {}, clientInfo: CLIENT }).trim(),
      signal: abort.signal,
    })
    if (!helloResponse.ok) return { ok: false, error: 'http-status', detail: `server answered HTTP ${helloResponse.status}` }
    const helloRaw = await helloResponse.text()
    const hello = parsePayload(helloResponse.headers.get('content-type')?.includes('event-stream') === true ? sseData(helloRaw) : helloRaw)
    if (hello?.result === undefined) return failure(hello)
    const session = helloResponse.headers.get('mcp-session-id')
    const listedResponse = await fetch(server.url!, {
      method: 'POST', headers: { ...headers, ...(session ? { 'mcp-session-id': session } : {}) },
      body: request(2, 'tools/list').trim(), signal: abort.signal,
    })
    if (!listedResponse.ok) return { ok: true, ...info(hello.result) }
    const listedRaw = await listedResponse.text()
    const listed = parsePayload(listedResponse.headers.get('content-type')?.includes('event-stream') === true ? sseData(listedRaw) : listedRaw)
    const count = toolCount(listed?.result)
    return { ok: true, ...info(hello.result), ...(count === undefined ? {} : { toolCount: count }) }
  } catch {
    return abort.signal.aborted
      ? { ok: false, error: 'timeout', detail: 'no MCP answer within the timeout' }
      : { ok: false, error: 'connect', detail: 'endpoint could not be reached' }
  } finally {
    clearTimeout(timer)
  }
}

export async function probeOverlayMcpServer(server: OverlayMcpServer, timeoutMs = 30_000): Promise<McpProbeResult> {
  return server.transport === 'stdio' ? await stdio(server, timeoutMs) : await http(server, timeoutMs)
}
