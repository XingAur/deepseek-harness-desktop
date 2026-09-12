/** Host allowlists so only overseas model APIs use the Desktop model proxy. */

const BIN_NAME = 'dsh-plugin-desktop'

/** Providers that may send traffic through the model proxy. */
export const DESKTOP_MODEL_PROXY_PROVIDERS = ['xai', 'openai-codex'] as const

/** One provider id accepted by `dsh-desktop.modelProxyProviders`. */
export type DesktopModelProxyProvider = (typeof DESKTOP_MODEL_PROXY_PROVIDERS)[number]

const PROVIDER_HOSTS: Readonly<Record<DesktopModelProxyProvider, readonly string[]>> = {
  xai: ['api.x.ai', 'x.ai'],
  'openai-codex': ['api.openai.com', 'openai.com', 'chatgpt.com', 'auth.openai.com'],
}

const DEFAULT_PROVIDERS: readonly DesktopModelProxyProvider[] = DESKTOP_MODEL_PROXY_PROVIDERS

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
    if (item !== 'xai' && item !== 'openai-codex') {
      throw new Error(`${BIN_NAME}: dsh-desktop.modelProxyProviders has unknown provider ${String(item)}`)
    }
    if (!parsed.includes(item)) parsed.push(item)
  }
  return parsed
}

/**
 * Hostnames (and parent suffixes) that should use the model proxy.
 * @param providers - selected provider ids.
 */
export function desktopModelProxyHosts(providers: readonly string[]): string[] {
  const hosts: string[] = []
  for (const provider of providers) {
    if (provider === 'xai' || provider === 'openai-codex') hosts.push(...PROVIDER_HOSTS[provider])
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
