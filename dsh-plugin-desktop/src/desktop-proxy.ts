/** Install Desktop outbound HTTP or SOCKS5 proxy policy for this process. */

import {
  desktopModelProxyHosts,
  resolveDesktopModelProxyUrl,
  type DesktopModelProxyCustomProvider,
  type DesktopModelProxyRouteInput,
} from './desktop-model-proxy.ts'
import {
  desktopProxyKind,
  parseDesktopProxyUrl,
} from './desktop-proxy-url.ts'

/** Minimal environment lookup used by the upstream HTTP proxy installer. */
export interface DesktopProxyEnvLookup {
  get(name: string): { readonly value: string } | undefined
}

/** Optional model-scoped proxy installed when the process-wide URL is empty. */
export interface DesktopModelProxyInstall {
  readonly url: string
  readonly providers: readonly string[]
  readonly extraHosts?: readonly string[]
  readonly customProviders?: readonly DesktopModelProxyCustomProvider[]
  readonly providerUrls?: Readonly<Record<string, string>>
}

function envWithHttpOverride(
  env: DesktopProxyEnvLookup,
  proxyUrl: string,
): DesktopProxyEnvLookup {
  return {
    get(name: string) {
      if (
        name === 'HTTP_PROXY'
        || name === 'http_proxy'
        || name === 'HTTPS_PROXY'
        || name === 'https_proxy'
      ) {
        return { value: proxyUrl }
      }
      return env.get(name)
    },
  }
}

function readEnvironmentProxyUrl(env: DesktopProxyEnvLookup): string {
  const raw = env.get('HTTPS_PROXY')?.value
    ?? env.get('https_proxy')?.value
    ?? env.get('HTTP_PROXY')?.value
    ?? env.get('http_proxy')?.value
    ?? ''
  try {
    return parseDesktopProxyUrl(raw)
  } catch {
    return ''
  }
}

async function installHttpProxyFromEnvironment(
  env: DesktopProxyEnvLookup,
  report: (message: string) => void,
): Promise<() => Promise<void>> {
  // Keep the specifier a variable: channels whose vendored runtime predates
  // the proxy package must not fail typecheck or launch.
  const proxyModuleSpecifier = '@deepseek-ai/dsh-http-proxy'
  const proxyModule = await import(proxyModuleSpecifier).catch(() => undefined) as
    | {
        installProxyFromEnvironment: (
          lookup: DesktopProxyEnvLookup,
          report: (message: string) => void,
        ) => Promise<() => Promise<void>>
      }
    | undefined
  if (proxyModule === undefined) return async () => {}
  return await proxyModule.installProxyFromEnvironment(env, report)
}

type UndiciDispatcher = {
  close(): Promise<void>
  on(event: string, listener: (...args: never[]) => void): UndiciDispatcher
}

type UndiciProxyRuntime = {
  Agent: new (opts?: {
    factory?: (origin: string | URL, options?: object) => UndiciDispatcher
  }) => UndiciDispatcher
  Pool: new (origin: string | URL, opts?: object) => UndiciDispatcher
  ProxyAgent: new (opts: string | { uri: string }) => UndiciDispatcher
  Socks5ProxyAgent: new (uri: string) => UndiciDispatcher
  getGlobalDispatcher: () => unknown
  setGlobalDispatcher: (dispatcher: unknown) => void
}

const PROCESS_PROXY_ENV_KEYS = [
  'HTTP_PROXY',
  'HTTPS_PROXY',
  'ALL_PROXY',
  'NO_PROXY',
  'http_proxy',
  'https_proxy',
  'all_proxy',
  'no_proxy',
  'NODE_USE_ENV_PROXY',
] as const

function clearProcessProxyEnv(): () => void {
  const previous: Partial<Record<(typeof PROCESS_PROXY_ENV_KEYS)[number], string | undefined>> = {}
  for (const name of PROCESS_PROXY_ENV_KEYS) {
    previous[name] = process.env[name]
    Reflect.deleteProperty(process.env, name)
  }
  return () => {
    for (const name of PROCESS_PROXY_ENV_KEYS) {
      const value = previous[name]
      if (value === undefined) Reflect.deleteProperty(process.env, name)
      else process.env[name] = value
    }
  }
}

async function loadUndici(): Promise<UndiciProxyRuntime | undefined> {
  return await import('undici').catch(() => undefined) as UndiciProxyRuntime | undefined
}

async function installSocksProxy(
  proxyUrl: string,
  report: (message: string) => void,
): Promise<() => Promise<void>> {
  const undici = await loadUndici()
  if (undici?.Socks5ProxyAgent === undefined) {
    report('SOCKS proxy support is unavailable in this runtime; connecting directly')
    return async () => {}
  }
  const previous = undici.getGlobalDispatcher()
  const agent = new undici.Socks5ProxyAgent(proxyUrl)
  undici.setGlobalDispatcher(agent)
  return async () => {
    undici.setGlobalDispatcher(previous)
    await agent.close()
  }
}

async function installHostAllowlistProxy(
  route: DesktopModelProxyRouteInput,
  report: (message: string) => void,
): Promise<() => Promise<void>> {
  const undici = await loadUndici()
  if (undici?.Agent === undefined || undici.ProxyAgent === undefined) {
    report('model proxy support is unavailable in this runtime; connecting directly')
    return async () => {}
  }
  const hosts = desktopModelProxyHosts(route.providers, route.extraHosts, route.customProviders)
  const hasUrl = route.sharedUrl !== ''
    || Object.values(route.providerUrls ?? {}).some(url => url.trim() !== '')
    || (route.customProviders ?? []).some(item => item.proxyUrl.trim() !== '')
  if (hosts.length === 0 || !hasUrl) return async () => {}
  const previous = undici.getGlobalDispatcher()
  const restoreEnv = clearProcessProxyEnv()
  const agent = new undici.Agent({
    factory(origin, options) {
      let hostname = ''
      try {
        hostname = (origin instanceof URL ? origin : new URL(String(origin))).hostname
      } catch {
        hostname = ''
      }
      const proxyUrl = hostname === '' ? '' : resolveDesktopModelProxyUrl(hostname, route)
      const kind = desktopProxyKind(proxyUrl)
      if (kind === 'socks' && undici.Socks5ProxyAgent !== undefined) {
        return new undici.Socks5ProxyAgent(proxyUrl)
      }
      if (kind === 'http') return new undici.ProxyAgent({ ...options, uri: proxyUrl })
      // One Pool per origin. Returning a shared Agent here is closed by undici
      // when that origin drains, which then breaks later direct fetches
      // (Zhipu, Yunxiao, and other domestic APIs).
      return new undici.Pool(origin, options)
    },
  })
  undici.setGlobalDispatcher(agent)
  report(`model proxy enabled for ${hosts.join(', ')}`)
  return async () => {
    undici.setGlobalDispatcher(previous)
    restoreEnv()
    await agent.close()
  }
}

/**
 * Install the outbound proxy for this Desktop generation.
 *
 * A settings process-wide `proxyUrl` still overrides every fetch. When that
 * field is empty, only selected overseas model hosts use `modelProxy` or the
 * inherited `HTTP_PROXY`; other destinations stay direct so domestic APIs are
 * not forced through a tunnel.
 * @param env - launch environment snapshot already layered by the launcher.
 * @param proxyUrl - Desktop settings value, empty to use the model allowlist.
 * @param report - operator-facing diagnostic; must not echo credentials.
 * @param modelProxy - allowlisted model proxy; empty url inherits HTTP_PROXY.
 */
export async function installDesktopOutboundProxy(
  env: DesktopProxyEnvLookup,
  proxyUrl: string,
  report: (message: string) => void,
  modelProxy?: DesktopModelProxyInstall,
): Promise<() => Promise<void>> {
  let parsed: string
  try {
    parsed = parseDesktopProxyUrl(proxyUrl)
  } catch {
    report('dsh-desktop.proxyUrl is not a usable proxy URL; applying the model-host allowlist instead')
    parsed = ''
  }
  const kind = desktopProxyKind(parsed)
  if (kind === 'http') {
    return await installHttpProxyFromEnvironment(envWithHttpOverride(env, parsed), report)
  }
  if (kind === 'socks') return await installSocksProxy(parsed, report)

  let modelUrl = ''
  try {
    modelUrl = parseDesktopProxyUrl(modelProxy?.url ?? '')
  } catch {
    report('dsh-desktop.modelProxyUrl is not a usable proxy URL; trying HTTP_PROXY for model hosts')
  }
  if (modelUrl === '') modelUrl = readEnvironmentProxyUrl(env)
  return await installHostAllowlistProxy({
    sharedUrl: modelUrl,
    providers: modelProxy?.providers ?? ['xai', 'openai-codex'],
    extraHosts: modelProxy?.extraHosts ?? [],
    customProviders: modelProxy?.customProviders ?? [],
    providerUrls: modelProxy?.providerUrls ?? {},
  }, report)
}

export {
  desktopProxyDiagnosticOrigin,
  desktopProxyKind,
  parseDesktopProxyUrl,
} from './desktop-proxy-url.ts'
