/** Per-provider model proxy: only overseas API hosts go through the tunnel. */

import { readFile } from 'node:fs/promises'
import { join } from 'node:path'
import { parse } from 'yaml'
import { readOverlayConfig, writeOverlayConfig } from './overlay-config.ts'


const MAX_PROXY_URL_BYTES = 2048
const HTTP_PROTOCOLS = new Set(['http:', 'https:'])
const DEFAULT_PROVIDERS = ['xai', 'openai-codex'] as const

const PROVIDER_HOSTS: Readonly<Record<string, readonly string[]>> = {
  xai: ['api.x.ai', 'x.ai'],
  'openai-codex': ['api.openai.com', 'openai.com', 'chatgpt.com', 'auth.openai.com'],
  anthropic: ['api.anthropic.com'],
  gemini: ['generativelanguage.googleapis.com'],
}

export interface ModelProxyConfig {
  readonly url: string
  readonly providers: readonly string[]
}

export function parseModelProxyUrl(value: unknown): string {
  if (value === undefined || value === '') return ''
  if (typeof value !== 'string') throw new Error('proxy url must be a string')
  const trimmed = value.trim()
  if (trimmed === '') return ''
  if (trimmed.length > MAX_PROXY_URL_BYTES) throw new Error('proxy url too long')
  const parsed = new URL(trimmed)
  if (!HTTP_PROTOCOLS.has(parsed.protocol)) throw new Error(`unsupported proxy scheme ${parsed.protocol}`)
  return trimmed
}

export function proxyDiagnosticOrigin(value: string): string {
  if (value === '') return ''
  const parsed = new URL(value)
  parsed.username = ''
  parsed.password = ''
  return `${parsed.protocol}//${parsed.host}`
}

export function hostsForProviders(providers: readonly string[]): string[] {
  const hosts = new Set<string>()
  for (const id of providers) {
    for (const host of PROVIDER_HOSTS[id] ?? []) hosts.add(host)
  }
  return [...hosts]
}

export function hostNeedsProxy(hostname: string, providers: readonly string[]): boolean {
  const host = hostname.toLowerCase()
  for (const allowed of hostsForProviders(providers)) {
    if (host === allowed || host.endsWith(`.${allowed}`)) return true
  }
  return false
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {}
}

function proxyFromRecord(source: Record<string, unknown>): ModelProxyConfig {
  let url = ''
  try {
    url = parseModelProxyUrl(source.modelProxyUrl)
  } catch {
    url = ''
  }
  const providers = Array.isArray(source.modelProxyProviders)
    ? source.modelProxyProviders.filter((item): item is string => typeof item === 'string' && item in PROVIDER_HOSTS)
    : [...DEFAULT_PROVIDERS]
  return { url, providers: providers.length > 0 ? providers : [...DEFAULT_PROVIDERS] }
}

export async function readModelProxyConfig(home: string): Promise<ModelProxyConfig> {
  const overlay = proxyFromRecord(await readOverlayConfig(home))
  if (overlay.url !== '') return overlay
  let document: unknown = {}
  try {
    document = parse(await readFile(join(home, 'settings.yaml'), 'utf8'))
  } catch {
    document = {}
  }
  const fromSettings = proxyFromRecord(asRecord(asRecord(document)['dsh-desktop']))
  if (fromSettings.url !== '') return fromSettings
  const envUrl = process.env.DSH_OVERLAY_MODEL_PROXY_URL
  if (typeof envUrl === 'string') {
    try {
      return { url: parseModelProxyUrl(envUrl), providers: overlay.providers }
    } catch {
      // Fall through to empty.
    }
  }
  return overlay
}

export async function writeModelProxyConfig(home: string, config: ModelProxyConfig): Promise<ModelProxyConfig> {
  const url = parseModelProxyUrl(config.url)
  const providers = config.providers.filter(id => id in PROVIDER_HOSTS)
  await writeOverlayConfig(home, { modelProxyUrl: url, modelProxyProviders: providers })
  return { url, providers }
}

type UndiciRuntime = {
  Agent: new (opts?: { factory?: (origin: string | URL, options?: object) => { close(): Promise<void> } }) => {
    close(): Promise<void>
  }
  Pool: new (origin: string | URL, opts?: object) => { close(): Promise<void> }
  ProxyAgent: new (opts: { uri: string } & object) => { close(): Promise<void> }
  getGlobalDispatcher: () => unknown
  setGlobalDispatcher: (dispatcher: unknown) => void
}

async function loadUndici(): Promise<UndiciRuntime | undefined> {
  return await import('undici').catch(() => undefined) as UndiciRuntime | undefined
}

/**
 * Route allowlisted model hosts through the overlay proxy; every other origin
 * stays on a dedicated Pool so domestic APIs (DeepSeek, Zhipu) stay direct.
 */
export async function installModelProxy(config: ModelProxyConfig, report: (message: string) => void): Promise<() => Promise<void>> {
  if (config.url === '' || config.providers.length === 0) return async () => {}
  const undici = await loadUndici()
  if (undici?.Agent === undefined || undici.ProxyAgent === undefined) {
    report('dsh-overlay-custom: undici proxy support unavailable; connecting directly')
    return async () => {}
  }
  const previous = undici.getGlobalDispatcher()
  const agent = new undici.Agent({
    factory(origin, options) {
      let hostname = ''
      try {
        hostname = (origin instanceof URL ? origin : new URL(String(origin))).hostname
      } catch {
        hostname = ''
      }
      if (hostNeedsProxy(hostname, config.providers)) {
        return new undici.ProxyAgent({ ...options, uri: config.url })
      }
      return new undici.Pool(origin, options)
    },
  })
  undici.setGlobalDispatcher(agent)
  report(`dsh-overlay-custom: model proxy ${proxyDiagnosticOrigin(config.url)} for ${hostsForProviders(config.providers).join(', ')}`)
  return async () => {
    undici.setGlobalDispatcher(previous)
    await agent.close()
  }
}
