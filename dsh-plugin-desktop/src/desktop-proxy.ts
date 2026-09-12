/** Install Desktop outbound HTTP or SOCKS5 proxy policy for this process. */

import {
  desktopModelProxyHosts,
  hostnameUsesModelProxy,
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

type UndiciProxyRuntime = {
  Agent: new (opts?: { factory?: (origin: string | URL) => { close(): Promise<void> } }) => {
    close(): Promise<void>
  }
  ProxyAgent: new (uri: string) => { close(): Promise<void> }
  Socks5ProxyAgent: new (uri: string) => { close(): Promise<void> }
  getGlobalDispatcher: () => unknown
  setGlobalDispatcher: (dispatcher: unknown) => void
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
  proxyUrl: string,
  hosts: readonly string[],
  report: (message: string) => void,
): Promise<() => Promise<void>> {
  const undici = await loadUndici()
  if (undici?.Agent === undefined || undici.ProxyAgent === undefined) {
    report('model proxy support is unavailable in this runtime; connecting directly')
    return async () => {}
  }
  const kind = desktopProxyKind(proxyUrl)
  if (kind === 'none' || hosts.length === 0) return async () => {}
  if (kind === 'socks' && undici.Socks5ProxyAgent === undefined) {
    report('SOCKS proxy support is unavailable in this runtime; connecting directly')
    return async () => {}
  }
  const previous = undici.getGlobalDispatcher()
  const proxied = kind === 'socks'
    ? new undici.Socks5ProxyAgent(proxyUrl)
    : new undici.ProxyAgent(proxyUrl)
  const direct = new undici.Agent()
  const agent = new undici.Agent({
    factory(origin) {
      try {
        const hostname = (origin instanceof URL ? origin : new URL(String(origin))).hostname
        return hostnameUsesModelProxy(hostname, hosts) ? proxied : direct
      } catch {
        return direct
      }
    },
  })
  undici.setGlobalDispatcher(agent)
  report(`model proxy enabled for ${hosts.join(', ')}`)
  return async () => {
    undici.setGlobalDispatcher(previous)
    await Promise.all([agent.close(), proxied.close(), direct.close()])
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
  const hosts = desktopModelProxyHosts(modelProxy?.providers ?? ['xai', 'openai-codex'])
  if (modelUrl === '' || hosts.length === 0) return async () => {}
  return await installHostAllowlistProxy(modelUrl, hosts, report)
}

export {
  desktopProxyDiagnosticOrigin,
  desktopProxyKind,
  parseDesktopProxyUrl,
} from './desktop-proxy-url.ts'
