import type { IncomingMessage, ServerResponse } from 'node:http'
import { describe, expect, it, vi } from 'vitest'
import DesktopSettingsController, {
  type DesktopSettingsControllerBootstrap,
} from '../src/desktop-settings-controller.ts'
import { handleDesktopMcpStateRequest } from '../src/desktop-settings-route.ts'
import {
  createDesktopSettingsApi,
  parseDesktopMcpStateView,
} from '../src/client/desktop-settings-api.ts'

const ORIGIN = 'http://127.0.0.1:43120'

function getRequest(origin = ORIGIN): IncomingMessage {
  return {
    method: 'GET',
    headers: { origin, host: '127.0.0.1:43120' },
    socket: { remoteAddress: '127.0.0.1' },
  } as unknown as IncomingMessage
}

function putRequest(body: string, origin = ORIGIN): IncomingMessage {
  return {
    method: 'PUT',
    headers: { origin, host: '127.0.0.1:43120', 'content-type': 'application/json' },
    socket: { remoteAddress: '127.0.0.1' },
    async * [Symbol.asyncIterator]() { yield Buffer.from(body) },
  } as unknown as IncomingMessage
}

function response(): ServerResponse & {
  body: string
  end: ReturnType<typeof vi.fn>
  setHeader: ReturnType<typeof vi.fn>
} {
  const res = {
    body: '',
    statusCode: 200,
    setHeader: vi.fn(),
    end: vi.fn((body?: string) => { res.body = body ?? '' }),
  }
  return res as unknown as ServerResponse & typeof res
}

const SERVERS = [
  {
    id: 'desktop-mcp-fs',
    serverName: 'fs',
    transport: 'stdio',
    command: 'npx',
    args: ['-y', '@modelcontextprotocol/server-fs'],
    envKeys: ['API_TOKEN'],
    cwd: null,
    url: null,
    headerKeys: [],
    disabled: false,
  },
  {
    id: 'desktop-mcp-web',
    serverName: 'web',
    transport: 'streamable-http',
    command: null,
    args: [],
    envKeys: [],
    cwd: null,
    url: 'http://127.0.0.1:3000/mcp',
    headerKeys: ['Authorization'],
    disabled: true,
  },
]

/** Overrides keep each property's type but never add `undefined` (exactOptionalPropertyTypes). */
function baseBootstrap(): DesktopSettingsControllerBootstrap {
  return {
    profiles: {
      current: { name: 'desktop', dir: '/profiles/desktop' },
      list: () => [],
      create: () => ({
        name: 'created',
        dir: '/profiles/created',
        exists: true,
        bundles: [],
        webCapable: true,
      }),
      prepareSelection: () => Promise.resolve({ restartRequired: false, restart: async () => undefined }),
    },
    readMarket: () => ({ requested: 'disabled', effective: 'disabled', legacyDefaulted: false }),
    selectMarket: async () => ({ requested: 'disabled', effective: 'disabled', legacyDefaulted: false }),
    readWeb: () => ({
      localUrl: 'http://127.0.0.1:43120',
      lanUrls: [],
      lanState: 'inactive',
      lanError: null,
      lanCaFingerprint: null,
      lanCaUrls: [],
    }),
    scheduleRestart: () => undefined,
    scheduleRecoveryRestart: () => undefined,
    openTerminal: () => undefined,
    reloadRenderer: () => undefined,
    toggleDeveloperTools: () => undefined,
    exportDiagnostics: () => undefined,
  }
}


describe('desktop mcp test route', () => {
  const postRequest = (body: string, origin = ORIGIN): IncomingMessage => ({
    method: 'POST',
    headers: { origin, host: '127.0.0.1:43120', 'content-type': 'application/json' },
    socket: { remoteAddress: '127.0.0.1' },
    async * [Symbol.asyncIterator]() { yield Buffer.from(body) },
  } as unknown as IncomingMessage)

  it('probes a row and returns the handshake outcome', async () => {
    const { handleDesktopMcpTestRequest } = await import('../src/desktop-settings-route.ts')
    const controller = new DesktopSettingsController({
      ...baseBootstrap(),
      probeMcp: async () => ({ ok: true, toolCount: 3, serverInfoName: 'fake' }),
    })
    const row = { id: 'desktop-mcp-fs', serverName: 'fs', transport: 'stdio', command: 'npx', args: ['-y', 'x'] }
    const req = postRequest(JSON.stringify({ id: row.id, row }))
    const res = response()
    await handleDesktopMcpTestRequest(req, res, ORIGIN, controller)
    expect(res.statusCode).toBe(200)
    const parsed = JSON.parse(res.body) as { ok: boolean; toolCount?: number }
    expect(parsed.ok).toBe(true)
    expect(parsed.toolCount).toBe(3)
  })

  it('rejects invalid bodies and wrong methods', async () => {
    const { handleDesktopMcpTestRequest } = await import('../src/desktop-settings-route.ts')
    const controller = new DesktopSettingsController(baseBootstrap())
    const bad = response()
    await handleDesktopMcpTestRequest(postRequest(JSON.stringify({ row: { transport: 'stdio' } })), bad, ORIGIN, controller)
    expect(bad.statusCode).toBe(400)
    const wrongMethod = { ...postRequest('{}'), method: 'GET' } as unknown as IncomingMessage
    const res2 = response()
    await handleDesktopMcpTestRequest(wrongMethod, res2, ORIGIN, controller)
    expect(res2.statusCode).toBe(405)
  })

  it('answers 500 when the launcher mounts no probe', async () => {
    const { handleDesktopMcpTestRequest } = await import('../src/desktop-settings-route.ts')
    const controller = new DesktopSettingsController(baseBootstrap())
    const res = response()
    const row = { id: 'desktop-mcp-fs', serverName: 'fs', transport: 'stdio', command: 'npx', args: ['-y', 'x'] }
    await handleDesktopMcpTestRequest(postRequest(JSON.stringify({ row })), res, ORIGIN, controller)
    expect(res.statusCode).toBe(500)
  })
})

describe('desktop mcp templates', () => {
  it('builds a postgres connection string from friendly fields', async () => {
    const { templateById } = await import('../src/client/desktop-mcp-templates.ts')
    const database = templateById('database')
    expect(database).toBeDefined()
    const built = database?.build({
      dbtype: 'postgresql', host: '192.168.1.10', port: '', user: 'his_ro', password: 'pw', database: 'hisdb',
    })
    expect(built).toMatchObject({ transport: 'stdio', command: 'npx' })
    if (built?.transport !== 'stdio') throw new Error('expected stdio build')
    expect(built.args[2]).toBe('postgresql://his_ro:pw@192.168.1.10:5432/hisdb')
  })

  it('builds an oracle uvx command with a service dsn and default port', async () => {
    const { templateById } = await import('../src/client/desktop-mcp-templates.ts')
    const built = templateById('database')?.build({
      dbtype: 'oracle', host: '192.168.1.8', port: '', user: 'scott', password: 'tiger', database: 'hisprd',
    })
    if (built?.transport !== 'stdio') throw new Error('expected stdio build')
    expect(built.command).toBe('uvx')
    expect(built.args).toContain('--dsn')
    expect(built.args).toContain('192.168.1.8:1521/hisprd')
  })

  it('maps mysql fields into env credentials', async () => {
    const { templateById } = await import('../src/client/desktop-mcp-templates.ts')
    const built = templateById('database')?.build({
      dbtype: 'mysql', host: 'db.corp', port: '3307', user: 'root', password: 'pw', database: 'ygt',
    })
    if (built?.transport !== 'stdio') throw new Error('expected stdio build')
    expect(built.env).toMatchObject({ MYSQL_HOST: 'db.corp', MYSQL_PORT: '3307', MYSQL_DB: 'ygt' })
  })

  it('sends the yunxiao bearer header against the official endpoint', async () => {
    const { templateById } = await import('../src/client/desktop-mcp-templates.ts')
    const built = templateById('yunxiao')?.build({ token: 'tok-1' })
    if (built?.transport !== 'streamable-http') throw new Error('expected http build')
    expect(built.url).toContain('openapi-rdc.aliyuncs.com/ai/mcp')
    expect(built.headers.Authorization).toBe('Bearer tok-1')
  })

  it('maps gitlab host and token into env', async () => {
    const { templateById } = await import('../src/client/desktop-mcp-templates.ts')
    const built = templateById('gitlab')?.build({ host: 'https://git.example.com/', token: 'gl-tk' })
    if (built?.transport !== 'stdio') throw new Error('expected stdio build')
    expect(built.env.GITLAB_API_URL).toBe('https://git.example.com/api/v4')
    expect(built.env.GITLAB_PERSONAL_ACCESS_TOKEN).toBe('gl-tk')
  })
})

describe('desktop mcp controller', () => {
  it('returns an empty list when the launcher mounts no MCP state', () => {
    const controller = new DesktopSettingsController(baseBootstrap())
    expect(controller.listMcp()).toEqual({ servers: [], restartRequired: true })
  })

  it('projects launcher rows without echoing secret values', () => {
    const controller = new DesktopSettingsController({
      ...baseBootstrap(),
      readMcp: () => [{
        id: 'desktop-mcp-fs',
        serverName: 'fs',
        transport: 'stdio',
        command: 'npx',
        env: { API_TOKEN: 'secret-value' },
      }],
    })
    const view = controller.listMcp()
    expect(view.restartRequired).toBe(true)
    expect(view.servers[0]).toMatchObject({ serverName: 'fs', envKeys: ['API_TOKEN'] })
    expect(JSON.stringify(view)).not.toContain('secret-value')
  })

  it('merges write rows, persists them, and restarts after the response', async () => {
    const writeMcp = vi.fn(async () => undefined)
    const scheduleRestart = vi.fn()
    const controller = new DesktopSettingsController({
      ...baseBootstrap(),
      readMcp: () => [{
        id: 'desktop-mcp-fs',
        serverName: 'fs',
        transport: 'stdio',
        command: 'npx',
        env: { API_TOKEN: 'stored' },
      }],
      writeMcp,
      scheduleRestart,
    })
    const operation = await controller.writeMcp({
      servers: [{
        id: 'desktop-mcp-fs',
        serverName: 'fs',
        transport: 'stdio',
        command: 'node',
        env: { NEW_KEY: 'v2', API_TOKEN: null },
      }],
    })
    expect(operation.response.accepted).toBe(true)
    expect(operation.response.restartScheduled).toBe(true)
    expect(writeMcp).toHaveBeenCalledWith([
      expect.objectContaining({
        id: 'desktop-mcp-fs',
        command: 'node',
        env: { NEW_KEY: 'v2' },
      }),
    ])
    expect(scheduleRestart).not.toHaveBeenCalled()
    await operation.afterResponse?.()
    expect(scheduleRestart).toHaveBeenCalledOnce()
  })

  it('rejects duplicate ids without persisting', async () => {
    const writeMcp = vi.fn(async () => undefined)
    const controller = new DesktopSettingsController({
      ...baseBootstrap(),
      readMcp: () => [],
      writeMcp,
    })
    await expect(controller.writeMcp({
      servers: [
        { id: 'dup', serverName: 'one', transport: 'stdio', command: 'a' },
        { id: 'dup', serverName: 'two', transport: 'stdio', command: 'b' },
      ],
    })).rejects.toThrow(/duplicate MCP server id/u)
    expect(writeMcp).not.toHaveBeenCalled()
  })

  it('fails when no MCP state is mounted', async () => {
    const controller = new DesktopSettingsController(baseBootstrap())
    await expect(controller.writeMcp({ servers: [] })).rejects.toThrow(/not mounted/u)
  })
})

describe('desktop mcp route', () => {
  it('serves the state over loopback same-origin GET', async () => {
    const controller = new DesktopSettingsController({
      ...baseBootstrap(),
      readMcp: () => [],
    })
    const res = response()
    await handleDesktopMcpStateRequest(getRequest(), res, ORIGIN, controller)
    expect(res.statusCode).toBe(200)
    expect(JSON.parse(res.body)).toEqual({ servers: [], restartRequired: true })
  })

  it('accepts a valid PUT, then 405s other methods and 403s cross-origin', async () => {
    const writeMcp = vi.fn(async () => undefined)
    const controller = new DesktopSettingsController({
      ...baseBootstrap(),
      readMcp: () => [],
      writeMcp,
    })
    const put = response()
    await handleDesktopMcpStateRequest(putRequest('{"servers":[]}'), put, ORIGIN, controller)
    expect(put.statusCode).toBe(200)
    expect(JSON.parse(put.body)).toEqual({ accepted: true, servers: [], restartScheduled: true })
    for (const req of [
      getRequest('https://example.com'),
      { method: 'POST', headers: { origin: ORIGIN } } as IncomingMessage,
    ]) {
      const res = response()
      await handleDesktopMcpStateRequest(req, res, ORIGIN, controller)
      expect(res.statusCode).toBe(req.method === 'GET' ? 403 : 405)
    }
  })

  it('rejects invalid write bodies with a 400 before touching state', async () => {
    const writeMcp = vi.fn(async () => undefined)
    const controller = new DesktopSettingsController({
      ...baseBootstrap(),
      readMcp: () => [],
      writeMcp,
    })
    for (const body of ['not json', '{"servers":"x"}', '{"servers":[{"id":"bad name!","serverName":"x","transport":"stdio"}]}']) {
      const res = response()
      await handleDesktopMcpStateRequest(putRequest(body), res, ORIGIN, controller)
      expect(res.statusCode).toBe(400)
    }
    expect(writeMcp).not.toHaveBeenCalled()
  })

  it('maps read failures to a generic 500 without native paths', async () => {
    const controller = new DesktopSettingsController({
      ...baseBootstrap(),
      readMcp: () => { throw new Error('/private/mcp/state.json collapsed') },
    })
    const res = response()
    await handleDesktopMcpStateRequest(getRequest(), res, ORIGIN, controller)
    expect(res.statusCode).toBe(500)
    expect(JSON.parse(res.body)).toEqual({ error: 'mcp state unavailable' })
  })
})

describe('desktop mcp state response parsing', () => {
  it('accepts a valid state response', () => {
    expect(parseDesktopMcpStateView({ servers: SERVERS, restartRequired: true })).toEqual({
      servers: SERVERS,
      restartRequired: true,
    })
  })

  it('rejects malformed shapes and duplicates', () => {
    expect(() => parseDesktopMcpStateView({ servers: SERVERS })).toThrow(/invalid Desktop MCP state/u)
    expect(() => parseDesktopMcpStateView({ servers: SERVERS, restartRequired: 'yes' })).toThrow(/invalid Desktop MCP state/u)
    expect(() => parseDesktopMcpStateView({ servers: [SERVERS[0], SERVERS[0]], restartRequired: true }))
      .toThrow(/duplicate/u)
    expect(() => parseDesktopMcpStateView({
      servers: [{ ...SERVERS[0], serverName: 'bad name!' }],
      restartRequired: true,
    })).toThrow(/invalid MCP server row/u)
  })
})

describe('desktop mcp client api', () => {
  it('parses a successful GET from the MCP endpoint', async () => {
    const fetcher = vi.fn(async (_path: string) => new Response(JSON.stringify({ servers: SERVERS, restartRequired: true }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })) as unknown as (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
    const api = createDesktopSettingsApi(fetcher)
    await expect(api.getMcp()).resolves.toEqual({ servers: SERVERS, restartRequired: true })
    expect(fetcher).toHaveBeenCalledWith('/api/desktop/mcp', expect.objectContaining({ method: 'GET' }))
  })

  it('sends PUT rows and parses the acknowledgement', async () => {
    const fetcher = vi.fn(async (_path: string, _init?: RequestInit) => new Response(JSON.stringify({
      accepted: true,
      servers: SERVERS,
      restartScheduled: true,
    }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })) as unknown as (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
    const api = createDesktopSettingsApi(fetcher)
    await expect(api.putMcp([{ id: 'desktop-mcp-fs', serverName: 'fs', transport: 'stdio', command: 'npx' }]))
      .resolves.toEqual({ accepted: true, servers: SERVERS, restartScheduled: true })
    expect(fetcher).toHaveBeenCalledWith('/api/desktop/mcp', expect.objectContaining({ method: 'PUT' }))
  })
})
