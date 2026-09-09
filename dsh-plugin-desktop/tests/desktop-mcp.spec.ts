import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import {
  DESKTOP_MCP_PACKAGE_NAME,
  desktopMcpInsertPatch,
  mergeDesktopMcpServer,
  parseDesktopMcpServer,
  parseDesktopMcpState,
  projectDesktopMcpServer,
  readDesktopMcpState,
  writeDesktopMcpState,
  type DesktopMcpServerState,
} from '../src/desktop-mcp.ts'
import { prepareDesktopProfile } from '../src/profile.ts'

const homes: string[] = []

function temporaryHome(): string {
  const home = mkdtempSync(join(tmpdir(), 'dsh-desktop-mcp-'))
  homes.push(home)
  return home
}

afterEach(() => {
  for (const home of homes.splice(0)) rmSync(home, { recursive: true, force: true })
})

const STDIO_ROW: DesktopMcpServerState = {
  id: 'desktop-mcp-fs',
  serverName: 'fs',
  transport: 'stdio',
  command: 'npx',
  args: ['-y', '@modelcontextprotocol/server-fs'],
  env: { API_TOKEN: 'secret-value' },
}

const HTTP_ROW: DesktopMcpServerState = {
  id: 'desktop-mcp-web',
  serverName: 'web',
  transport: 'streamable-http',
  url: 'http://127.0.0.1:3000/mcp',
  headers: { Authorization: 'Bearer secret' },
}

describe('desktop MCP state parsing', () => {
  it('accepts valid stdio and streamable-http rows', () => {
    expect(parseDesktopMcpServer(STDIO_ROW)).toEqual(STDIO_ROW)
    expect(parseDesktopMcpServer(HTTP_ROW)).toEqual(HTTP_ROW)
  })

  it('rejects invalid server names, transports, and transport-missing fields', () => {
    expect(() => parseDesktopMcpServer({ ...STDIO_ROW, serverName: 'bad name!' })).toThrow(/serverName/)
    expect(() => parseDesktopMcpServer({ ...STDIO_ROW, transport: 'websocket' })).toThrow(/transport/)
    expect(() => parseDesktopMcpServer({ id: 'x', serverName: 'x', transport: 'stdio' })).toThrow(/command/)
    expect(() => parseDesktopMcpServer({ id: 'x', serverName: 'x', transport: 'streamable-http' })).toThrow(/url/)
    expect(() => parseDesktopMcpServer({ ...STDIO_ROW, args: ['ok', 3] })).toThrow(/args/)
    expect(() => parseDesktopMcpServer({ ...HTTP_ROW, headers: { A: 1 } })).toThrow(/headers/)
  })

  it('rejects duplicate ids and duplicate server names across the state', () => {
    expect(() => parseDesktopMcpState({ version: 1, servers: [STDIO_ROW, STDIO_ROW] })).toThrow(/duplicate MCP server id/)
    const clash = { ...HTTP_ROW, id: 'desktop-mcp-web2' }
    expect(() => parseDesktopMcpState({ version: 1, servers: [HTTP_ROW, clash] })).toThrow(/duplicate MCP serverName web/)
    expect(() => parseDesktopMcpState({ version: 2, servers: [] })).toThrow(/version/)
  })
})

describe('desktop MCP state file', () => {
  it('reads a missing file as the empty state', () => {
    const home = temporaryHome()
    expect(readDesktopMcpState(join(home, 'mcp-servers', 'state.json'))).toEqual({ version: 1, servers: [] })
  })

  it('writes and re-reads state atomically with private permissions', async () => {
    const path = join(temporaryHome(), 'mcp-servers', 'state.json')
    await writeDesktopMcpState(path, [STDIO_ROW, HTTP_ROW])
    expect(readDesktopMcpState(path)).toEqual({ version: 1, servers: [STDIO_ROW, HTTP_ROW] })
    const content = readFileSync(path, 'utf8')
    expect(content).toContain('"API_TOKEN": "secret-value"')
  })

  it('fails loudly on corrupt state instead of silently dropping rows', () => {
    const home = temporaryHome()
    const path = join(home, 'state.json')
    writeFileSync(path, '{"version":1,"servers":[{"id":"x"}]}')
    expect(() => readDesktopMcpState(path)).toThrow(/invalid MCP state/)
  })
})

describe('desktop MCP patch layer', () => {
  it('inserts only enabled rows with the MCP client package identity', () => {
    const patch = desktopMcpInsertPatch([
      STDIO_ROW,
      { ...HTTP_ROW, disabled: true },
    ])
    expect(patch.insert).toHaveLength(1)
    const row = patch.insert[0] as unknown as Record<string, unknown>
    expect(row.id).toBe('desktop-mcp-fs')
    expect(row.name).toBe(DESKTOP_MCP_PACKAGE_NAME)
    const config = row.config as Record<string, unknown>
    expect(config).toMatchObject({ serverName: 'fs', transport: 'stdio', command: 'npx' })
    expect(config.env).toEqual({ API_TOKEN: 'secret-value' })
  })

  it('injects the desktop layer into the composed profile patches', () => {
    const home = temporaryHome()
    const prepared = prepareDesktopProfile(undefined, home, 'darwin', 'desktop', undefined, undefined, {}, [
      desktopMcpInsertPatch([STDIO_ROW, HTTP_ROW]),
    ])
    const flattened = prepared.patches.flatMap(patch => Array.isArray(patch.insert) ? patch.insert : [])
    const mcpRows = flattened.filter(row => (row as { id?: string }).id?.startsWith('desktop-mcp-'))
    expect(mcpRows.map(row => (row as { id: string }).id).sort()).toEqual(['desktop-mcp-fs', 'desktop-mcp-web'])
  })
})

describe('desktop MCP projection and secret merge', () => {
  it('projects rows without echoing secret values', () => {
    const view = projectDesktopMcpServer(STDIO_ROW)
    expect(view).toEqual({
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
    })
    expect(JSON.stringify(view)).not.toContain('secret-value')
  })

  it('merges secret patches with set, keep, and delete semantics', () => {
    const merged = mergeDesktopMcpServer(STDIO_ROW, {
      id: 'desktop-mcp-fs',
      serverName: 'fs',
      transport: 'stdio',
      command: 'npx',
      args: ['-y', 'other'],
      env: { NEW_KEY: 'v2', API_TOKEN: null },
    })
    expect(merged.args).toEqual(['-y', 'other'])
    expect(merged.env).toEqual({ NEW_KEY: 'v2' })

    const kept = mergeDesktopMcpServer(STDIO_ROW, {
      id: 'desktop-mcp-fs',
      serverName: 'fs',
      transport: 'stdio',
      command: 'npx',
    })
    expect(kept.env).toEqual({ API_TOKEN: 'secret-value' })

    const cleared = mergeDesktopMcpServer(STDIO_ROW, {
      id: 'desktop-mcp-fs',
      serverName: 'fs',
      transport: 'stdio',
      command: 'npx',
      env: { API_TOKEN: null },
    })
    expect(cleared.env).toBeUndefined()
  })
})

describe('probeDesktopMcpServer', () => {
  const FAKE_SERVER_SCRIPT = String.raw`
    let buf = '';
    process.stdin.on('data', chunk => {
      buf += chunk.toString('utf8');
      let index;
      while ((index = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, index).trim();
        buf = buf.slice(index + 1);
        if (line.length === 0) continue;
        try {
          const message = JSON.parse(line);
          if (message.id === 1) {
            process.stdout.write(JSON.stringify({
              jsonrpc: '2.0', id: 1,
              result: { protocolVersion: '2024-11-05', serverInfo: { name: 'fake-mcp', version: '1.2.3' }, capabilities: {} },
            }) + '\n');
          }
          if (message.id === 2) {
            process.stdout.write(JSON.stringify({
              jsonrpc: '2.0', id: 2, result: { tools: [{ name: 'a' }, { name: 'b' }] },
            }) + '\n');
          }
        } catch { /* ignore */ }
      }
    });
  `

  it('completes a stdio handshake and counts advertised tools', async () => {
    const { probeDesktopMcpServer } = await import('../src/desktop-mcp.ts')
    const result = await probeDesktopMcpServer(
      { transport: 'stdio', command: process.execPath, args: ['-e', FAKE_SERVER_SCRIPT] },
      { timeoutMs: 8_000 },
    )
    expect(result.ok, JSON.stringify(result)).toBe(true)
    expect(result.toolCount).toBe(2)
    expect(result.serverInfoName).toBe('fake-mcp')
    expect(result.serverInfoVersion).toBe('1.2.3')
  }, 15_000)

  it('reports a stable timeout when the server never answers', async () => {
    const { probeDesktopMcpServer } = await import('../src/desktop-mcp.ts')
    const result = await probeDesktopMcpServer(
      { transport: 'stdio', command: process.execPath, args: ['-e', 'setInterval(() => {}, 1000)'] },
      { timeoutMs: 600 },
    )
    expect(result.ok).toBe(false)
    expect(result.error).toBe('timeout')
  }, 10_000)
})
