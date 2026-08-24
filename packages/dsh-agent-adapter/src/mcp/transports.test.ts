import { describe, expect, it } from 'vitest'
import {
  createMcpHttpTransport,
  createMcpSseTransport,
  createMcpStdioTransport,
} from './transports.js'
import { MCP_PROTOCOL_VERSION, McpTransportError, type McpRequest } from './client.js'

describe('MCP transport adapters', () => {
  it('uses bounded JSONL stdio frames and rejects mismatched responses', async () => {
    const writes: string[] = []
    const process = {
      async start() {},
      async write(line: string) { writes.push(line) },
      async readLine() {
        return JSON.stringify({ jsonrpc: '2.0', id: 'mcp-1', result: { ok: true } })
      },
      async kill() {},
    }
    const transport = createMcpStdioTransport(process)
    await transport.connect()
    const result = await transport.request({ id: 'mcp-1', method: 'initialize', params: { protocolVersion: MCP_PROTOCOL_VERSION } })
    expect(result).toEqual({ ok: true })
    expect(JSON.parse(writes[0])).toMatchObject({ jsonrpc: '2.0', id: 'mcp-1', method: 'initialize' })

    const mismatched = createMcpStdioTransport({
      ...process,
      async readLine() { return JSON.stringify({ jsonrpc: '2.0', id: 'other', result: {} }) },
    })
    await mismatched.connect()
    await expect(mismatched.request({ id: 'mcp-2', method: 'tools/list', params: {} })).rejects.toMatchObject({ code: 'malformed-message' })
  })

  it('restricts HTTP endpoints and uses redirect rejection with bounded responses', async () => {
    const calls: Array<{ input: string; init?: RequestInit }> = []
    const fetchImpl = (async (input: string | URL, init?: RequestInit) => {
      calls.push({ input: String(input), init })
      return new Response(JSON.stringify({ jsonrpc: '2.0', id: 'mcp-1', result: { tools: [] } }), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      })
    }) as typeof fetch
    const transport = createMcpHttpTransport('https://mcp.example.test/rpc', { fetchImpl })
    await transport.connect()
    await transport.request({ id: 'mcp-1', method: 'tools/list', params: {} })
    expect(calls[0].init).toMatchObject({ method: 'POST', redirect: 'error' })
    expect(calls[0].init?.headers).toMatchObject({ 'content-type': 'application/json' })
    await expect(() => createMcpHttpTransport('http://mcp.example.test/rpc')).toThrow(McpTransportError)
  })

  it('correlates SSE responses and cancels only the matching request', async () => {
    let onMessage: ((message: string) => void) | undefined
    const sent: string[] = []
    const channel = {
      async connect(listener: (message: string) => void) { onMessage = listener },
      async send(message: string) {
        sent.push(message)
        const request = JSON.parse(message) as McpRequest
        if (request.method === 'ping') onMessage?.(JSON.stringify({ jsonrpc: '2.0', id: request.id, result: { ok: true } }))
      },
      async close() {},
    }
    const transport = createMcpSseTransport(channel)
    await transport.connect()
    await expect(transport.request({ id: 'mcp-3', method: 'ping', params: {} })).resolves.toEqual({ ok: true })
    expect(JSON.parse(sent[0])).toMatchObject({ jsonrpc: '2.0', id: 'mcp-3' })

    const controller = new AbortController()
    const pending = transport.request({ id: 'mcp-4', method: 'slow', params: {} }, controller.signal)
    controller.abort()
    await expect(pending).rejects.toMatchObject({ code: 'cancelled' })
  })
})
