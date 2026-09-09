/**
 * Desktop-managed MCP server rows.
 *
 * The settings Skills/MCP pages treat external MCP servers as desktop-private
 * state: rows live in a JSON file under the Desktop user-data directory and
 * are injected as one Cordis patch layer while the profile generation is
 * composed. Nothing is written into the user-owned profile documents, so a
 * recomposition can never corrupt hand-edited patches and removing the state
 * file restores the stock profile. Row changes apply on the next Host
 * restart, matching the Claude Desktop restart-required contract.
 */
import { mkdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, isAbsolute } from 'node:path'
import type { EntryOptions } from '@deepseek-ai/cordis-plugin-loader'
import { withFileLock, writeFileAtomic } from '@deepseek-ai/dsh-atomic-write'
import { DESKTOP_PACKAGE_NAME } from './product-identity.ts'
import type { DesktopMcpServerView, DesktopMcpStateRequest } from './desktop-settings-contract.ts'

const BIN_NAME = DESKTOP_PACKAGE_NAME

/** Hard cap so a corrupt state file cannot exhaust memory while parsing. */
const MAX_STATE_BYTES = 256 * 1024

/** Per-profile state file mode pair, mirroring the plugin-management state. */
const STATE_FILE_MODE = 0o600
const STATE_DIRECTORY_MODE = 0o700

/** Transport accepted by `@deepseek-ai/dsh-mcp-client` rows. */
export type DesktopMcpTransport = 'stdio' | 'streamable-http'

/** One desktop-owned MCP server row as stored and edited by the settings page. */
export interface DesktopMcpServerState {
  /** Stable row identity; also the Cordis entry id (`desktop-mcp-<name>`). */
  readonly id: string
  /** Tool namespace segment; `[A-Za-z0-9_-]{1,32}` per the MCP client contract. */
  readonly serverName: string
  /** Transport selecting which config fields are meaningful. */
  readonly transport: DesktopMcpTransport
  /** stdio: executable to spawn. */
  readonly command?: string
  /** stdio: argument vector. */
  readonly args?: readonly string[]
  /** stdio: extra environment merged over the scrubbed ambient env. */
  readonly env?: Readonly<Record<string, string>>
  /** stdio: working directory for the spawned process. */
  readonly cwd?: string
  /** streamable-http: endpoint URL. */
  readonly url?: string
  /** streamable-http: extra request headers. */
  readonly headers?: Readonly<Record<string, string>>
  /** Disabled rows stay in state but are never injected into the profile. */
  readonly disabled?: boolean
}

/** Parsed desktop MCP state document. */
export interface DesktopMcpState {
  readonly version: 1
  readonly servers: readonly DesktopMcpServerState[]
}

const MCP_SERVER_NAME_PATTERN = /^[A-Za-z0-9_-]{1,32}$/
const MCP_ID_PATTERN = /^[A-Za-z0-9_-]{1,64}$/

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function parseStringField(row: Record<string, unknown>, field: string, label: string): string | undefined {
  const value = row[field]
  if (value === undefined) return undefined
  if (typeof value !== 'string' || value.length === 0) {
    throw new Error(`${BIN_NAME}: ${label} field ${field} must be a non-empty string`)
  }
  return value
}

function parseStringRecord(row: Record<string, unknown>, field: string, label: string): Record<string, string> | undefined {
  const value = row[field]
  if (value === undefined) return undefined
  if (!isPlainRecord(value)) throw new Error(`${BIN_NAME}: ${label} field ${field} must be an object`)
  for (const [key, entry] of Object.entries(value)) {
    if (typeof entry !== 'string') {
      throw new Error(`${BIN_NAME}: ${label} field ${field}.${key} must be a string`)
    }
  }
  return value as Record<string, string>
}

/** Validate one state row, accepting only the fields this module owns. */
export function parseDesktopMcpServer(value: unknown): DesktopMcpServerState {
  if (!isPlainRecord(value)) throw new Error(`${BIN_NAME}: MCP server row must be an object`)
  const id = parseStringField(value, 'id', 'MCP server')
  if (id === undefined || !MCP_ID_PATTERN.test(id)) {
    throw new Error(`${BIN_NAME}: MCP server id must match ${MCP_ID_PATTERN.source}`)
  }
  const serverName = parseStringField(value, 'serverName', 'MCP server')
  if (serverName === undefined || !MCP_SERVER_NAME_PATTERN.test(serverName)) {
    throw new Error(`${BIN_NAME}: MCP serverName must match ${MCP_SERVER_NAME_PATTERN.source}`)
  }
  const transport = value.transport
  if (transport !== 'stdio' && transport !== 'streamable-http') {
    throw new Error(`${BIN_NAME}: MCP server ${serverName} transport must be "stdio" or "streamable-http"`)
  }
  const command = parseStringField(value, 'command', `MCP server ${serverName}`)
  const url = parseStringField(value, 'url', `MCP server ${serverName}`)
  const cwd = parseStringField(value, 'cwd', `MCP server ${serverName}`)
  const rawArgs = value.args
  if (rawArgs !== undefined && (!Array.isArray(rawArgs) || rawArgs.some(entry => typeof entry !== 'string'))) {
    throw new Error(`${BIN_NAME}: MCP server ${serverName} args must be an array of strings`)
  }
  const env = parseStringRecord(value, 'env', `MCP server ${serverName}`)
  const headers = parseStringRecord(value, 'headers', `MCP server ${serverName}`)
  const row: DesktopMcpServerState = {
    id,
    serverName,
    transport,
    ...(command === undefined ? {} : { command }),
    ...(rawArgs === undefined ? {} : { args: Object.freeze([...rawArgs] as string[]) }),
    ...(env === undefined ? {} : { env: Object.freeze({ ...env }) }),
    ...(cwd === undefined ? {} : { cwd }),
    ...(url === undefined ? {} : { url }),
    ...(headers === undefined ? {} : { headers: Object.freeze({ ...headers }) }),
    ...(value.disabled === undefined ? {} : { disabled: value.disabled === true }),
  }
  if (row.transport === 'stdio' && row.command === undefined) {
    throw new Error(`${BIN_NAME}: MCP server ${serverName} requires a command for the stdio transport`)
  }
  if (row.transport === 'streamable-http' && row.url === undefined) {
    throw new Error(`${BIN_NAME}: MCP server ${serverName} requires a url for the streamable-http transport`)
  }
  return row
}

/** Parse a whole state document with duplicate-identity rejection. */
export function parseDesktopMcpState(value: unknown): DesktopMcpState {
  if (!isPlainRecord(value)) throw new Error(`${BIN_NAME}: MCP state must be an object`)
  if (value.version !== 1) throw new Error(`${BIN_NAME}: MCP state version must be 1`)
  if (!Array.isArray(value.servers)) throw new Error(`${BIN_NAME}: MCP state servers must be an array`)
  const servers = value.servers.map(parseDesktopMcpServer)
  const ids = new Set<string>()
  const names = new Set<string>()
  for (const server of servers) {
    if (ids.has(server.id)) throw new Error(`${BIN_NAME}: duplicate MCP server id ${server.id}`)
    if (names.has(server.serverName)) {
      throw new Error(`${BIN_NAME}: duplicate MCP serverName ${server.serverName}`)
    }
    ids.add(server.id)
    names.add(server.serverName)
  }
  return { version: 1, servers: Object.freeze(servers) }
}

/**
 * Read the desktop MCP state; a missing file is the empty state, while a
 * corrupt one fails loudly so the settings page can surface it instead of
 * silently dropping rows the user believes are active.
 */
export function readDesktopMcpState(statePath: string): DesktopMcpState {
  let content: string
  try {
    const stat = statSync(statePath)
    if (!stat.isFile() || stat.isSymbolicLink()) {
      throw new Error(`${BIN_NAME}: MCP state must be a regular file`)
    }
    if (stat.size > MAX_STATE_BYTES) throw new Error(`${BIN_NAME}: MCP state is too large`)
    content = readFileSync(statePath, 'utf8')
  } catch (cause) {
    if ((cause as NodeJS.ErrnoException).code === 'ENOENT') return { version: 1, servers: [] }
    throw cause
  }
  try {
    return parseDesktopMcpState(JSON.parse(content) as unknown)
  } catch (cause) {
    throw new Error(`${BIN_NAME}: invalid MCP state at ${statePath}: ${cause instanceof Error ? cause.message : String(cause)}`)
  }
}

/** Persist the state atomically under a lock, creating private directories. */
export async function writeDesktopMcpState(statePath: string, servers: readonly DesktopMcpServerState[]): Promise<void> {
  if (!isAbsolute(statePath) || statePath.includes('\0')) {
    throw new Error(`${BIN_NAME}: MCP state path must be absolute and contain no NUL`)
  }
  const state = parseDesktopMcpState({ version: 1, servers: [...servers] })
  // The lock sibling is created inside the target directory, so the private
  // directory must exist before the lock is taken.
  mkdirSync(dirname(statePath), { recursive: true, mode: STATE_DIRECTORY_MODE })
  await withFileLock(statePath, async () => {
    await writeFileAtomic(statePath, `${JSON.stringify(state, undefined, 2)}\n`, {
      mode: STATE_FILE_MODE,
      dirMode: STATE_DIRECTORY_MODE,
    })
  })
}

/** Cordis row package providing MCP client entries. */
export const DESKTOP_MCP_PACKAGE_NAME = '@deepseek-ai/dsh-mcp-client'

/**
 * Compose the desktop MCP patch layer: one insert patch carrying an entry per
 * enabled server. Rows live only in this layer, so recomposition always
 * reflects the current state and disabled rows simply disappear.
 */
export function desktopMcpInsertPatch(servers: readonly DesktopMcpServerState[]): { insert: EntryOptions[] } {
  const rows: EntryOptions[] = servers
    .filter(server => server.disabled !== true)
    .map(server => ({
      id: `desktop-mcp-${server.serverName}`,
      name: DESKTOP_MCP_PACKAGE_NAME,
      config: {
        serverName: server.serverName,
        transport: server.transport,
        ...(server.transport === 'stdio'
          ? {
            command: server.command,
            ...(server.args === undefined ? {} : { args: [...server.args] }),
            ...(server.env === undefined ? {} : { env: { ...server.env } }),
            ...(server.cwd === undefined ? {} : { cwd: server.cwd }),
          }
          : {
            url: server.url,
            ...(server.headers === undefined ? {} : { headers: { ...server.headers } }),
          }),
      },
    }))
  return { insert: rows }
}

/**
 * Project one state row for the renderer. Secret values never cross the
 * API boundary: the settings UI edits them blind through the write-merge
 * contract and can only see which keys exist.
 */
export function projectDesktopMcpServer(server: DesktopMcpServerState): DesktopMcpServerView {
  return Object.freeze({
    id: server.id,
    serverName: server.serverName,
    transport: server.transport,
    command: server.command ?? null,
    args: Object.freeze([...(server.args ?? [])]),
    envKeys: Object.freeze(Object.keys(server.env ?? {}).sort()),
    cwd: server.cwd ?? null,
    url: server.url ?? null,
    headerKeys: Object.freeze(Object.keys(server.headers ?? {}).sort()),
    disabled: server.disabled === true,
  })
}

type SecretPatch = Readonly<Record<string, string | null>> | undefined

/** Apply the write-merge semantics: set strings, delete `null`s, keep absent. */
function mergeSecrets(existing: Readonly<Record<string, string>> | undefined, patch: SecretPatch): Record<string, string> | undefined {
  if (patch === undefined) return existing === undefined ? undefined : { ...existing }
  const merged: Record<string, string> = { ...(existing ?? {}) }
  for (const [key, value] of Object.entries(patch)) {
    if (value === null) delete merged[key]
    else merged[key] = value
  }
  return Object.keys(merged).length === 0 ? undefined : merged
}

/**
 * Merge one incoming write row into its stored row. Rows without a stored
 * predecessor pass through; secret patches follow the three-state contract
 * documented on `DesktopMcpStateRequest`.
 */
export function mergeDesktopMcpServer(existing: DesktopMcpServerState | undefined, incoming: DesktopMcpStateRequest['servers'][number]): DesktopMcpServerState {
  const base: Partial<DesktopMcpServerState> = existing ?? {}
  const env = mergeSecrets(base.env, incoming.env)
  const headers = mergeSecrets(base.headers, incoming.headers)
  return {
    id: incoming.id,
    serverName: incoming.serverName,
    transport: incoming.transport,
    ...(incoming.command === undefined ? {} : { command: incoming.command }),
    ...(incoming.args === undefined ? {} : { args: Object.freeze([...incoming.args]) }),
    ...(env === undefined ? {} : { env: Object.freeze(env) }),
    ...(incoming.cwd === undefined ? {} : { cwd: incoming.cwd }),
    ...(incoming.url === undefined ? {} : { url: incoming.url }),
    ...(headers === undefined ? {} : { headers: Object.freeze(headers) }),
    ...(incoming.disabled === undefined ? {} : { disabled: incoming.disabled === true }),
  }
}
