/** Overlay-owned MCP server state and renderer-safe projections. */

import { readOverlayConfig, writeOverlayConfig } from './overlay-config.ts'

export type OverlayMcpTransport = 'stdio' | 'streamable-http'

export interface OverlayMcpServer {
  readonly id: string
  readonly serverName: string
  readonly transport: OverlayMcpTransport
  readonly command?: string
  readonly args?: readonly string[]
  readonly env?: Readonly<Record<string, string>>
  readonly cwd?: string
  readonly url?: string
  readonly headers?: Readonly<Record<string, string>>
  readonly disabled?: boolean
}

export interface OverlayMcpServerWrite {
  readonly id: string
  readonly serverName: string
  readonly transport: OverlayMcpTransport
  readonly command?: string
  readonly args?: readonly string[]
  readonly env?: Readonly<Record<string, string | null>>
  readonly cwd?: string
  readonly url?: string
  readonly headers?: Readonly<Record<string, string | null>>
  readonly disabled?: boolean
}

const NAME = /^[A-Za-z0-9_-]{1,32}$/u
const ID = /^[A-Za-z0-9_-]{1,64}$/u
const MAX_SERVERS = 64
const MAX_TEXT = 2048

function record(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : undefined
}

function optionalText(value: unknown, field: string): string | undefined {
  if (value === undefined) return undefined
  if (typeof value !== 'string' || value.length === 0 || value.length > MAX_TEXT) {
    throw new Error(`${field} must be a non-empty bounded string`)
  }
  return value
}

function stringArray(value: unknown, field: string): readonly string[] | undefined {
  if (value === undefined) return undefined
  if (!Array.isArray(value) || value.length > 256
    || value.some(entry => typeof entry !== 'string' || entry.length > MAX_TEXT)) {
    throw new Error(`${field} must be a bounded string array`)
  }
  return [...value] as string[]
}

function stringMap(value: unknown, field: string): Readonly<Record<string, string>> | undefined {
  if (value === undefined) return undefined
  const map = record(value)
  if (map === undefined || Object.keys(map).length > 256) throw new Error(`${field} must be an object`)
  const result: Record<string, string> = {}
  for (const [key, entry] of Object.entries(map)) {
    if (key.length === 0 || key.length > 256 || typeof entry !== 'string' || entry.length > MAX_TEXT) {
      throw new Error(`${field} contains an invalid entry`)
    }
    result[key] = entry
  }
  return result
}

export function parseOverlayMcpServer(value: unknown): OverlayMcpServer {
  const row = record(value)
  if (row === undefined) throw new Error('MCP server must be an object')
  if (typeof row.id !== 'string' || !ID.test(row.id)) throw new Error('invalid MCP server id')
  if (typeof row.serverName !== 'string' || !NAME.test(row.serverName)) throw new Error('invalid MCP server name')
  if (row.transport !== 'stdio' && row.transport !== 'streamable-http') throw new Error('invalid MCP transport')
  if (row.disabled !== undefined && typeof row.disabled !== 'boolean') throw new Error('invalid MCP disabled flag')
  const parsed: OverlayMcpServer = {
    id: row.id,
    serverName: row.serverName,
    transport: row.transport,
    ...optionalText(row.command, 'command') === undefined ? {} : { command: optionalText(row.command, 'command') },
    ...stringArray(row.args, 'args') === undefined ? {} : { args: stringArray(row.args, 'args') },
    ...stringMap(row.env, 'env') === undefined ? {} : { env: stringMap(row.env, 'env') },
    ...optionalText(row.cwd, 'cwd') === undefined ? {} : { cwd: optionalText(row.cwd, 'cwd') },
    ...optionalText(row.url, 'url') === undefined ? {} : { url: optionalText(row.url, 'url') },
    ...stringMap(row.headers, 'headers') === undefined ? {} : { headers: stringMap(row.headers, 'headers') },
    ...(row.disabled === undefined ? {} : { disabled: row.disabled }),
  }
  if (parsed.transport === 'stdio' && parsed.command === undefined) throw new Error('stdio MCP server requires command')
  if (parsed.transport === 'streamable-http' && parsed.url === undefined) throw new Error('HTTP MCP server requires url')
  return parsed
}

export function parseOverlayMcpServers(value: unknown): readonly OverlayMcpServer[] {
  if (value === undefined) return []
  if (!Array.isArray(value) || value.length > MAX_SERVERS) throw new Error('mcpServers must be a bounded array')
  const servers = value.map(parseOverlayMcpServer)
  const ids = new Set<string>()
  const names = new Set<string>()
  for (const server of servers) {
    if (ids.has(server.id)) throw new Error(`duplicate MCP server id: ${server.id}`)
    if (names.has(server.serverName)) throw new Error(`duplicate MCP server name: ${server.serverName}`)
    ids.add(server.id)
    names.add(server.serverName)
  }
  return servers
}

export async function readOverlayMcpServers(home: string): Promise<readonly OverlayMcpServer[]> {
  return parseOverlayMcpServers((await readOverlayConfig(home)).mcpServers)
}

function mergeSecretMap(
  current: Readonly<Record<string, string>> | undefined,
  patch: Readonly<Record<string, string | null>> | undefined,
): Readonly<Record<string, string>> | undefined {
  if (patch === undefined) return current === undefined ? undefined : { ...current }
  const next: Record<string, string> = { ...(current ?? {}) }
  for (const [key, value] of Object.entries(patch)) {
    if (value === null) delete next[key]
    else next[key] = value
  }
  return Object.keys(next).length === 0 ? undefined : next
}

export function mergeOverlayMcpServer(
  current: OverlayMcpServer | undefined,
  value: OverlayMcpServerWrite,
): OverlayMcpServer {
  return parseOverlayMcpServer({
    id: value.id,
    serverName: value.serverName,
    transport: value.transport,
    ...(value.command === undefined ? {} : { command: value.command }),
    ...(value.args === undefined ? {} : { args: value.args }),
    ...(mergeSecretMap(current?.env, value.env) === undefined ? {} : { env: mergeSecretMap(current?.env, value.env) }),
    ...(value.cwd === undefined ? {} : { cwd: value.cwd }),
    ...(value.url === undefined ? {} : { url: value.url }),
    ...(mergeSecretMap(current?.headers, value.headers) === undefined ? {} : { headers: mergeSecretMap(current?.headers, value.headers) }),
    ...(value.disabled === undefined ? {} : { disabled: value.disabled }),
  })
}

export async function writeOverlayMcpServers(
  home: string,
  writes: readonly OverlayMcpServerWrite[],
): Promise<readonly OverlayMcpServer[]> {
  const current = await readOverlayMcpServers(home)
  const servers = parseOverlayMcpServers(writes.map(write =>
    mergeOverlayMcpServer(current.find(server => server.id === write.id), write)))
  await writeOverlayConfig(home, { mcpServers: servers })
  return servers
}

export function projectOverlayMcpServer(server: OverlayMcpServer): Record<string, unknown> {
  return {
    id: server.id,
    serverName: server.serverName,
    transport: server.transport,
    command: server.command ?? null,
    args: [...(server.args ?? [])],
    envKeys: Object.keys(server.env ?? {}).sort(),
    cwd: server.cwd ?? null,
    url: server.url ?? null,
    headerKeys: Object.keys(server.headers ?? {}).sort(),
    disabled: server.disabled === true,
  }
}

export function resolvedMcpArgs(server: OverlayMcpServer): readonly string[] {
  const env = server.env ?? {}
  return (server.args ?? []).map(argument => argument.replace(/\$\{([A-Za-z_][A-Za-z0-9_]*)\}/gu, (match, key: string) => env[key] ?? match))
}

export function mcpClientConfig(server: OverlayMcpServer): Record<string, unknown> {
  return server.transport === 'stdio'
    ? {
        transport: 'stdio', serverName: server.serverName, command: server.command,
        args: [...resolvedMcpArgs(server)], env: { ...(server.env ?? {}) }, cwd: server.cwd ?? '',
      }
    : {
        transport: 'streamable-http', serverName: server.serverName, url: server.url,
        headers: { ...(server.headers ?? {}) },
      }
}
