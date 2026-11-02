/** Overlay-owned model proxy configuration. Network routing lives at the model call boundary. */

import { randomUUID } from 'node:crypto'
import { mkdir, readFile, rename, rm, writeFile } from 'node:fs/promises'
import { dirname, join, resolve } from 'node:path'
import { parse, stringify } from 'yaml'
import { parseProxyBypass } from './model-proxy-routing.ts'
import type { SystemProxySnapshot } from './system-proxy.ts'

const MAX_PROXY_URL_BYTES = 2048
const HTTP_PROTOCOLS = new Set(['http:', 'https:'])
const MODES = new Set(['system', 'manual', 'direct'])
const OVERLAY_FILE = 'overlay-custom.yaml'

export type ModelProxyMode = 'system' | 'manual' | 'direct'
export interface ModelProxyConfig {
  readonly mode: ModelProxyMode
  /** Internal only. May contain credentials; use modelProxyConfigView for responses. */
  readonly url: string
  readonly bypass: readonly string[]
}

const DEFAULT_CONFIG: ModelProxyConfig = Object.freeze({ mode: 'system', url: '', bypass: Object.freeze([]) })

export function parseModelProxyUrl(value: unknown): string {
  if (value === undefined || value === '') return ''
  if (typeof value !== 'string') throw new Error('invalid-url')
  const trimmed = value.trim()
  if (!trimmed) return ''
  if (Buffer.byteLength(trimmed) > MAX_PROXY_URL_BYTES) throw new Error('url-too-long')
  // URL() otherwise accepts whitespace/newlines and normalizes backslashes.
  if (/[\s\\\u0000-\u001f\u007f]/.test(trimmed)) throw new Error('invalid-url')
  let parsed: URL
  try { parsed = new URL(trimmed) } catch { throw new Error('invalid-url') }
  if (!HTTP_PROTOCOLS.has(parsed.protocol)) throw new Error('unsupported-proxy-scheme')
  if (!/^https?:\/\//i.test(trimmed) || !parsed.hostname || parsed.pathname !== '/' || parsed.search || parsed.hash) throw new Error('invalid-url')
  // Explicit empty/zero ports and empty query/fragment suffixes must not disappear
  // into URL normalization and accidentally select another endpoint.
  const authority = trimmed.slice(trimmed.indexOf('://') + 3).split('/')[0]!
  if (!authority || /:$/.test(authority) || parsed.port === '0' || /[?#]/.test(trimmed)) throw new Error('invalid-url')
  return trimmed
}

/** Intentionally excludes all userinfo, paths and query parameters. */
export function proxyDiagnosticOrigin(value: string): string {
  if (!value) return ''
  try {
    const parsed = new URL(parseModelProxyUrl(value))
    return parsed.origin
  } catch {
    return ''
  }
}

function asRecord(value: unknown, error = 'invalid-config'): Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw new Error(error)
  return value as Record<string, unknown>
}

/** PATCH semantics: an omitted URL preserves the complete stored credential URL. */
export function parseModelProxyConfig(input: unknown, previous: ModelProxyConfig = DEFAULT_CONFIG): ModelProxyConfig {
  const source = asRecord(input)
  const mode = Object.hasOwn(source, 'mode') ? source.mode : previous.mode
  if (typeof mode !== 'string' || !MODES.has(mode)) throw new Error('invalid-mode')
  const url = Object.hasOwn(source, 'url') ? parseModelProxyUrl(source.url) : parseModelProxyUrl(previous.url)
  const bypass = parseProxyBypass(Object.hasOwn(source, 'bypass') ? source.bypass : previous.bypass)
  if (mode === 'manual' && !url) throw new Error('manual-url-required')
  return Object.freeze({ mode: mode as ModelProxyMode, url, bypass })
}

async function readOptionalYaml(home: string, file: string): Promise<Record<string, unknown>> {
  let text: string
  try { text = await readFile(join(home, file), 'utf8') } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return {}
    throw new Error('config-read-failed')
  }
  try {
    const document: unknown = parse(text)
    return document === null || document === undefined ? {} : asRecord(document, 'config-read-failed')
  } catch {
    throw new Error('config-read-failed')
  }
}

async function configFromOverlay(home: string, overlay: Record<string, unknown>): Promise<ModelProxyConfig> {
  const input: Record<string, unknown> = {}
  if (Object.hasOwn(overlay, 'modelProxyMode')) input.mode = overlay.modelProxyMode
  if (Object.hasOwn(overlay, 'modelProxyBypass')) input.bypass = overlay.modelProxyBypass
  if (Object.hasOwn(overlay, 'modelProxyUrl')) {
    input.url = overlay.modelProxyUrl
  } else {
    // One-way compatibility with old desktop settings. An explicitly empty
    // overlay URL wins; a removed password must not reappear from legacy data.
    const settings = await readOptionalYaml(home, 'settings.yaml')
    const legacy = settings['dsh-desktop']
    if (legacy !== undefined && legacy !== null) {
      const source = asRecord(legacy, 'config-read-failed')
      if (Object.hasOwn(source, 'modelProxyUrl')) input.url = source.modelProxyUrl
    }
  }
  // Legacy provider lists are deliberately neither read nor interpreted.
  // Legacy URLs remain available in manual mode, but do not opt into it.
  return parseModelProxyConfig(input)
}

export async function readModelProxyConfig(home: string): Promise<ModelProxyConfig> {
  return configFromOverlay(home, await readOptionalYaml(home, OVERLAY_FILE))
}

// Serializes proxy edits within this process; callers still own runtime activation.
const writes = new Map<string, Promise<unknown>>()

export async function writeModelProxyConfig(home: string, input: unknown): Promise<ModelProxyConfig> {
  const directory = resolve(home)
  const preceding = writes.get(directory) ?? Promise.resolve()
  const operation = preceding.catch(() => {}).then(async () => {
    const overlay = await readOptionalYaml(directory, OVERLAY_FILE)
    const source = asRecord(input)
    const complete = ['mode', 'url', 'bypass'].every(key => Object.hasOwn(source, key))
    const config = parseModelProxyConfig(input, complete ? DEFAULT_CONFIG : await configFromOverlay(directory, overlay))
    const next = {
      ...overlay, modelProxyMode: config.mode, modelProxyUrl: config.url, modelProxyBypass: [...config.bypass],
    }
    const path = join(directory, OVERLAY_FILE)
    const temporary = join(directory, `.${OVERLAY_FILE}.${randomUUID()}.tmp`)
    // Both rename endpoints and cleanup paths are fixed children of this home.
    if (dirname(path) !== directory || dirname(temporary) !== directory) throw new Error('config-write-failed')
    try {
      await mkdir(directory, { recursive: true, mode: 0o700 })
      await writeFile(temporary, `${stringify(next)}\n`, { encoding: 'utf8', mode: 0o600, flag: 'wx' })
      await rename(temporary, path)
    } catch {
      await rm(temporary, { force: true }).catch(() => {})
      throw new Error('config-write-failed')
    }
    return config
  })
  writes.set(directory, operation)
  try { return await operation } finally {
    if (writes.get(directory) === operation) writes.delete(directory)
  }
}

/** The only configuration projection intended for browser/API consumers. */
export function modelProxyConfigView(config: ModelProxyConfig, system: SystemProxySnapshot) {
  let hasCredentials = false
  if (config.url) {
    try {
      const url = new URL(config.url)
      hasCredentials = Boolean(url.username || url.password)
    } catch { /* Invalid URLs are never reflected. */ }
  }
  return {
    mode: config.mode,
    url: proxyDiagnosticOrigin(config.url),
    hasCredentials,
    bypass: [...config.bypass],
    system: {
      status: system.status,
      httpProxy: proxyDiagnosticOrigin(system.httpProxy),
      httpsProxy: proxyDiagnosticOrigin(system.httpsProxy),
      bypass: [...system.bypass],
      ...(system.error === undefined ? {} : { error: system.error }),
    },
  }
}
