import { mkdirSync, mkdtempSync, rmSync, symlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  MCP_PROTOCOL_VERSION,
  McpClient,
  McpTransportError,
  createMcpPermissionPolicy,
  type McpRequest,
  type McpTransport,
} from './client.js'

class FakeTransport implements McpTransport {
  readonly requests: McpRequest[] = []
  constructor(private readonly responses: Record<string, unknown>) {}
  async connect(): Promise<void> {}
  async request(request: McpRequest): Promise<unknown> {
    this.requests.push(request)
    return this.responses[request.method]
  }
  async close(): Promise<void> {}
}

class TimeoutTransport implements McpTransport {
  signal: AbortSignal | undefined
  async connect(): Promise<void> {}
  async request(request: McpRequest, signal?: AbortSignal): Promise<unknown> {
    if (request.method === 'initialize') return { protocolVersion: MCP_PROTOCOL_VERSION, serverInfo: { name: 'demo', version: '1.0.0' } }
    if (request.method === 'tools/list') return { tools: [{ name: 'read_file', effect: 'read', inputSchema: {} }] }
    this.signal = signal
    return await new Promise<unknown>(() => {})
  }
  async close(): Promise<void> {}
}

class ClosingTransport extends FakeTransport {
  closeCalls = 0
  override async close(): Promise<void> { this.closeCalls += 1 }
}

class HangingConnectTransport implements McpTransport {
  closeCalls = 0
  signal: AbortSignal | undefined
  async connect(signal?: AbortSignal): Promise<void> {
    this.signal = signal
    await new Promise<void>((_, reject) => signal?.addEventListener('abort', () => reject(new McpTransportError('cancelled', '连接已取消')), { once: true }))
  }
  async request(): Promise<unknown> { return undefined }
  async close(): Promise<void> { this.closeCalls += 1 }
}

describe('McpClient', () => {
  it('performs a versioned handshake and discovers bounded tools', async () => {
    const transport = new FakeTransport({
      initialize: { protocolVersion: MCP_PROTOCOL_VERSION, serverInfo: { name: 'demo', version: '1.0.0' } },
      'tools/list': { tools: [{ name: 'read_file', effect: 'read', inputSchema: { type: 'object' } }] },
    })
    const client = new McpClient('server-a', transport)
    await client.connect()
    expect(client.tools()).toEqual([{ name: 'read_file', effect: 'read', inputSchema: { type: 'object' } }])
    expect(transport.requests[0]).toMatchObject({ method: 'initialize', params: { protocolVersion: MCP_PROTOCOL_VERSION } })
  })

  it('requires approval for undeclared or newly expanded tools', async () => {
    const transport = new FakeTransport({
      initialize: { protocolVersion: MCP_PROTOCOL_VERSION, serverInfo: { name: 'demo', version: '1.0.0' } },
      'tools/list': { tools: [{ name: 'write_file', effect: 'write', inputSchema: { type: 'object' } }] },
      'tools/call': { content: [{ type: 'text', text: 'ok' }] },
    })
    const client = new McpClient('server-a', transport, {
      permission: createMcpPermissionPolicy([{ serverId: 'server-a', toolName: 'read_file', effect: 'read', taskId: 'task-a', scope: '/workspace' }]),
    })
    await client.connect()
    await expect(client.callTool('write_file', {}, { taskId: 'task-a', scope: '/workspace' })).rejects.toMatchObject({ code: 'approval-required' })
  })

  it('rejects protocol mismatch, malformed results, and oversized output', async () => {
    const mismatchTransport = new ClosingTransport({ initialize: { protocolVersion: 'mcp/v0' } })
    const mismatch = new McpClient('server-a', mismatchTransport)
    await expect(mismatch.connect()).rejects.toMatchObject({ code: 'protocol-mismatch' })
    expect(mismatchTransport.closeCalls).toBe(1)

    const malformed = new McpClient('server-a', new FakeTransport({
      initialize: { protocolVersion: MCP_PROTOCOL_VERSION, serverInfo: { name: 'demo', version: '1.0.0' } },
      'tools/list': { tools: [{ name: 'read_file', effect: 'read', inputSchema: {} }] },
      'tools/call': { unexpected: true },
    }), { permission: () => 'allow' })
    await malformed.connect()
    await expect(malformed.callTool('read_file', {}, { taskId: 'task-a', scope: '/workspace' })).rejects.toMatchObject({ code: 'malformed-message' })

    const oversized = new McpClient('server-a', new FakeTransport({
      initialize: { protocolVersion: MCP_PROTOCOL_VERSION, serverInfo: { name: 'demo', version: '1.0.0' } },
      'tools/list': { tools: [{ name: 'read_file', effect: 'read', inputSchema: {} }] },
      'tools/call': { content: [{ type: 'text', text: 'x'.repeat(17 * 1024) }] },
    }), { permission: () => 'allow' })
    await oversized.connect()
    await expect(oversized.callTool('read_file', {}, { taskId: 'task-a', scope: '/workspace' })).rejects.toMatchObject({ code: 'output-limit' })

    const unknownResultField = new McpClient('server-a', new FakeTransport({
      initialize: { protocolVersion: MCP_PROTOCOL_VERSION, serverInfo: { name: 'demo', version: '1.0.0' } },
      'tools/list': { tools: [{ name: 'read_file', effect: 'read', inputSchema: {} }] },
      'tools/call': { content: [{ type: 'text', text: 'ok', unexpected: 'secret' }] },
    }), { permission: () => 'allow' })
    await unknownResultField.connect()
    await expect(unknownResultField.callTool('read_file', {}, { taskId: 'task-a', scope: '/workspace' })).rejects.toMatchObject({ code: 'malformed-message' })
  })

  it('aborts the underlying transport when a request times out', async () => {
    const transport = new TimeoutTransport()
    const client = new McpClient('server-a', transport, { timeoutMs: 5, permission: () => 'allow' })
    await client.connect()
    await expect(client.callTool('read_file', {}, { taskId: 'task-a', scope: '/workspace' })).rejects.toMatchObject({ code: 'timeout' })
    expect(transport.signal?.aborted).toBe(true)
  })

  it('times out and closes a transport that hangs during connect', async () => {
    const transport = new HangingConnectTransport()
    const client = new McpClient('server-a', transport, { timeoutMs: 5 })
    await expect(client.connect()).rejects.toMatchObject({ code: 'timeout' })
    expect(transport.closeCalls).toBe(1)
    expect(transport.signal?.aborted).toBe(true)
  })

  it('does not let parent traversal escape a granted filesystem scope', () => {
    const policy = createMcpPermissionPolicy([{ serverId: 'server-a', toolName: 'read_file', effect: 'read', taskId: 'task-a', scope: '/workspace' }])
    const tool = { name: 'read_file', effect: 'read' as const, inputSchema: {} }
    expect(policy('server-a', tool, { taskId: 'task-a', scope: '/workspace/../outside' })).toBe('approval-required')
  })

  it('does not follow a symlink outside a granted filesystem scope', () => {
    if (process.platform === 'win32') return
    const root = mkdtempSync(join(tmpdir(), 'dsh-mcp-'))
    const workspace = join(root, 'workspace')
    const outside = join(root, 'outside')
    mkdirSync(workspace)
    mkdirSync(outside)
    symlinkSync(outside, join(workspace, 'link'), 'dir')
    try {
      const policy = createMcpPermissionPolicy([{ serverId: 'server-a', toolName: 'read_file', effect: 'read', taskId: 'task-a', scope: workspace }])
      const tool = { name: 'read_file', effect: 'read' as const, inputSchema: {} }
      expect(policy('server-a', tool, { taskId: 'task-a', scope: join(workspace, 'link', 'secret.txt') })).toBe('approval-required')
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  it('requires approval for a dangling symlink inside a granted filesystem scope', () => {
    if (process.platform === 'win32') return
    const root = mkdtempSync(join(tmpdir(), 'dsh-mcp-'))
    const workspace = join(root, 'workspace')
    mkdirSync(workspace)
    symlinkSync(join(root, 'missing'), join(workspace, 'dangling'), 'dir')
    try {
      const policy = createMcpPermissionPolicy([{ serverId: 'server-a', toolName: 'read_file', effect: 'read', taskId: 'task-a', scope: workspace }])
      const tool = { name: 'read_file', effect: 'read' as const, inputSchema: {} }
      expect(policy('server-a', tool, { taskId: 'task-a', scope: join(workspace, 'dangling', 'secret.txt') })).toBe('approval-required')
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  it('maps cancellation and remote failures without exposing response bodies', async () => {
    const transport: McpTransport = {
      async connect() {},
      async request(request) {
        if (request.method === 'initialize') return { protocolVersion: MCP_PROTOCOL_VERSION, serverInfo: { name: 'demo', version: '1.0.0' } }
        if (request.method === 'tools/list') return { tools: [{ name: 'read_file', effect: 'read', inputSchema: {} }] }
        throw new McpTransportError('remote-error', 'upstream failed', { body: 'secret-token' })
      },
      async close() {},
    }
    const client = new McpClient('server-a', transport, { permission: () => 'allow' })
    await client.connect()
    await expect(client.callTool('read_file', {}, { taskId: 'task-a', scope: '/workspace' })).rejects.toMatchObject({ code: 'remote-error', message: 'upstream failed' })
    try {
      await client.callTool('read_file', {}, { taskId: 'task-a', scope: '/workspace' })
    } catch (error) {
      expect(JSON.stringify(error)).not.toContain('secret-token')
    }
  })
})
