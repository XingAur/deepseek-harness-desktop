/** Host allowlists so only overseas model APIs use the Desktop model proxy. */

const BIN_NAME = 'dsh-plugin-desktop'

/** Providers that may send traffic through the model proxy. */
export const DESKTOP_MODEL_PROXY_PROVIDERS = ['xai', 'openai-codex', 'anthropic', 'gemini'] as const

/** One provider id accepted by `dsh-desktop.modelProxyProviders`. */
export type DesktopModelProxyProvider = (typeof DESKTOP_MODEL_PROXY_PROVIDERS)[number]

/** Region group of one built-in provider, driving one-click presets in the UI. */
export type DesktopModelProxyProviderRegion = 'overseas'

/** Static provider metadata consumed by the settings UI for grouping/presets. */
export interface DesktopModelProxyProviderInfo {
  readonly id: DesktopModelProxyProvider
  readonly region: DesktopModelProxyProviderRegion
}

/**
 * Built-in provider metadata. Every built-in today is an overseas API that
 * usually needs a VPN/proxy from mainland networks; domestic providers stay
 * direct by design and never appear here.
 */
export const DESKTOP_MODEL_PROXY_PROVIDER_INFO: readonly DesktopModelProxyProviderInfo[] = [
  { id: 'xai', region: 'overseas' },
  { id: 'openai-codex', region: 'overseas' },
  { id: 'anthropic', region: 'overseas' },
  { id: 'gemini', region: 'overseas' },
]

/** All built-in provider ids in one region — the "select the whole group" preset. */
export function desktopModelProxyProvidersInRegion(
  region: DesktopModelProxyProviderRegion,
): DesktopModelProxyProvider[] {
  return DESKTOP_MODEL_PROXY_PROVIDER_INFO
    .filter(info => info.region === region)
    .map(info => info.id)
}

const PROVIDER_HOSTS: Readonly<Record<DesktopModelProxyProvider, readonly string[]>> = {
  xai: ['api.x.ai', 'x.ai'],
  'openai-codex': ['api.openai.com', 'openai.com', 'chatgpt.com', 'auth.openai.com'],
  anthropic: ['api.anthropic.com'],
  gemini: ['generativelanguage.googleapis.com'],
}

const DEFAULT_PROVIDERS: readonly DesktopModelProxyProvider[] = ['xai', 'openai-codex']

/**
 * Parse the persisted model-proxy provider list.
 * @param value - untrusted settings value.
 */
export function parseDesktopModelProxyProviders(value: unknown): DesktopModelProxyProvider[] {
  if (value === undefined) return [...DEFAULT_PROVIDERS]
  if (!Array.isArray(value)) {
    throw new Error(`${BIN_NAME}: dsh-desktop.modelProxyProviders must be an array`)
  }
  const parsed: DesktopModelProxyProvider[] = []
  for (const item of value) {
    if (!DESKTOP_MODEL_PROXY_PROVIDERS.includes(item as DesktopModelProxyProvider)) {
      throw new Error(`${BIN_NAME}: dsh-desktop.modelProxyProviders has unknown provider ${String(item)}`)
    }
    if (!parsed.includes(item)) parsed.push(item)
  }
  return parsed
}

const HOST_PATTERN = /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$/u

/**
 * Normalize one extra model-proxy host. Accepts a hostname or a URL.
 * Rejects wildcards, single-label names like `com`, and empty values.
 * @param value - untrusted host or URL.
 * @returns lowercase hostname, or undefined when invalid.
 */
export function normalizeDesktopModelProxyHost(value: string): string | undefined {
  const trimmed = value.trim().toLowerCase()
  if (trimmed === '') return undefined
  let host = trimmed
  if (host.includes('://')) {
    try {
      host = new URL(host).hostname
    } catch {
      return undefined
    }
  } else {
    host = host.split('/')[0]?.split(':')[0] ?? ''
  }
  if (!HOST_PATTERN.test(host)) return undefined
  return host
}

/** One user-added model that should use the same proxy as Grok/Codex. */
export interface DesktopModelProxyCustomProvider {
  readonly name: string
  readonly hosts: string[]
  /** Dedicated proxy URL; empty uses the shared model proxy. */
  readonly proxyUrl: string
}

/**
 * Parse user-added model-proxy providers (name + API hosts).
 * @param value - untrusted settings value.
 */
export function parseDesktopModelProxyCustomProviders(value: unknown): DesktopModelProxyCustomProvider[] {
  if (value === undefined) return []
  if (!Array.isArray(value)) {
    throw new Error(`${BIN_NAME}: dsh-desktop.modelProxyCustomProviders must be an array`)
  }
  const parsed: DesktopModelProxyCustomProvider[] = []
  const names = new Set<string>()
  for (const item of value) {
    if (typeof item !== 'object' || item === null || Array.isArray(item)) {
      throw new Error(`${BIN_NAME}: dsh-desktop.modelProxyCustomProviders entries must be objects`)
    }
    const row = item as Record<string, unknown>
    if (typeof row.name !== 'string' || row.name.trim() === '') {
      throw new Error(`${BIN_NAME}: dsh-desktop.modelProxyCustomProviders name must be a non-empty string`)
    }
    const name = row.name.trim()
    if (names.has(name.toLowerCase())) continue
    names.add(name.toLowerCase())
    const proxyUrl = typeof row.proxyUrl === 'string' ? row.proxyUrl.trim() : ''
    parsed.push({ name, hosts: parseDesktopModelProxyExtraHosts(row.hosts), proxyUrl })
  }
  return parsed.filter(item => item.hosts.length > 0)
}

/**
 * Parse extra hostnames that also use the model proxy.
 * @param value - untrusted settings value.
 */
export function parseDesktopModelProxyExtraHosts(value: unknown): string[] {
  if (value === undefined) return []
  if (!Array.isArray(value)) {
    throw new Error(`${BIN_NAME}: dsh-desktop.modelProxyExtraHosts must be an array`)
  }
  const hosts: string[] = []
  for (const item of value) {
    if (typeof item !== 'string') {
      throw new Error(`${BIN_NAME}: dsh-desktop.modelProxyExtraHosts entries must be hostnames`)
    }
    const host = normalizeDesktopModelProxyHost(item)
    if (host === undefined) {
      throw new Error(`${BIN_NAME}: dsh-desktop.modelProxyExtraHosts has invalid host ${item}`)
    }
    if (!hosts.includes(host)) hosts.push(host)
  }
  return hosts
}

/**
 * Hostnames (and parent suffixes) that should use the model proxy.
 * @param providers - selected provider ids.
 * @param extraHosts - additional hostnames from settings.
 */
export function desktopModelProxyHosts(
  providers: readonly string[],
  extraHosts: readonly string[] = [],
  customProviders: readonly DesktopModelProxyCustomProvider[] = [],
): string[] {
  const hosts: string[] = []
  for (const provider of providers) {
    if (provider === 'xai' || provider === 'openai-codex' || provider === 'anthropic' || provider === 'gemini') {
      hosts.push(...PROVIDER_HOSTS[provider])
    }
  }
  for (const extra of extraHosts) {
    if (!hosts.includes(extra)) hosts.push(extra)
  }
  for (const custom of customProviders) {
    for (const extra of custom.hosts) {
      if (!hosts.includes(extra)) hosts.push(extra)
    }
  }
  return hosts
}

/**
 * Whether one request hostname belongs to the model-proxy allowlist.
 * @param hostname - URL hostname.
 * @param hosts - allowlisted suffixes.
 */
export function hostnameUsesModelProxy(hostname: string, hosts: readonly string[]): boolean {
  const host = hostname.toLowerCase()
  return hosts.some(allowed => host === allowed || host.endsWith(`.${allowed}`))
}

/**
 * Parse optional per-provider proxy URLs. Empty URL means use the shared model proxy.
 * @param value - untrusted settings value.
 */
export function parseDesktopModelProxyProviderUrls(value: unknown): Record<string, string> {
  if (value === undefined) return {}
  if (!Array.isArray(value)) {
    throw new Error(`${BIN_NAME}: dsh-desktop.modelProxyProviderUrls must be an array`)
  }
  const parsed: Record<string, string> = {}
  for (const item of value) {
    if (typeof item !== 'object' || item === null || Array.isArray(item)) {
      throw new Error(`${BIN_NAME}: dsh-desktop.modelProxyProviderUrls entries must be objects`)
    }
    const row = item as Record<string, unknown>
    if (typeof row.provider !== 'string' || row.provider.trim() === '') {
      throw new Error(`${BIN_NAME}: dsh-desktop.modelProxyProviderUrls provider must be a string`)
    }
    if (typeof row.proxyUrl !== 'string') {
      throw new Error(`${BIN_NAME}: dsh-desktop.modelProxyProviderUrls proxyUrl must be a string`)
    }
    const proxyUrl = row.proxyUrl.trim()
    if (proxyUrl === '') continue
    parsed[row.provider.trim()] = proxyUrl
  }
  return parsed
}

export interface DesktopModelProxyRouteInput {
  readonly sharedUrl: string
  readonly providers: readonly string[]
  readonly providerUrls?: Readonly<Record<string, string>>
  readonly extraHosts?: readonly string[]
  readonly customProviders?: readonly DesktopModelProxyCustomProvider[]
}

/**
 * Pick the proxy URL for one request hostname, or empty for a direct connection.
 * @param hostname - request hostname.
 * @param input - shared URL, selected providers, and optional overrides.
 */
export function resolveDesktopModelProxyUrl(hostname: string, input: DesktopModelProxyRouteInput): string {
  const host = hostname.toLowerCase()
  for (const provider of input.providers) {
    if (provider !== 'xai' && provider !== 'openai-codex' && provider !== 'anthropic' && provider !== 'gemini') continue
    if (!hostnameUsesModelProxy(host, PROVIDER_HOSTS[provider])) continue
    const override = input.providerUrls?.[provider]?.trim() ?? ''
    return override === '' ? input.sharedUrl : override
  }
  for (const custom of input.customProviders ?? []) {
    if (!hostnameUsesModelProxy(host, custom.hosts)) continue
    const override = custom.proxyUrl.trim()
    return override === '' ? input.sharedUrl : override
  }
  if (hostnameUsesModelProxy(host, input.extraHosts ?? [])) return input.sharedUrl
  return ''
}
