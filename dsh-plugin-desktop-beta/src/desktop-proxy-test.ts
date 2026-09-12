/** Probe whether a proxy can reach the Grok API without saving settings. */

import { desktopProxyKind, parseDesktopProxyUrl } from './desktop-proxy-url.ts'

/** Hosts this probe hits; a response of any HTTP status means the tunnel works. */
export const DESKTOP_MODEL_PROXY_TEST_ORIGIN = 'https://api.x.ai/'

/** Stable failure codes returned to the renderer. Never include credentials. */
export const DESKTOP_PROXY_TEST_CODES = [
  'ok',
  'invalid-url',
  'no-proxy',
  'proxy-refused',
  'proxy-auth',
  'timeout',
  'tls',
  'dns',
  'target-unreachable',
  'unknown',
] as const

/** One probe outcome code. */
export type DesktopProxyTestCode = (typeof DESKTOP_PROXY_TEST_CODES)[number]

/** Renderer-safe probe result. */
export interface DesktopProxyTestResult {
  readonly ok: boolean
  readonly code: DesktopProxyTestCode
}

const TEST_TIMEOUT_MS = 8_000

function environmentProxyUrl(): string {
  const raw = process.env.HTTPS_PROXY ?? process.env.https_proxy
    ?? process.env.HTTP_PROXY ?? process.env.http_proxy ?? ''
  try {
    return parseDesktopProxyUrl(raw)
  } catch {
    return ''
  }
}

/**
 * Map a thrown probe failure to a renderer-safe code.
 * @param cause - undici or Node error.
 */
export function classifyDesktopProxyTestError(cause: unknown): DesktopProxyTestCode {
  const error = cause as {
    name?: string
    code?: string
    message?: string
    cause?: { code?: string; message?: string }
  }
  const code = `${error.code ?? ''} ${error.cause?.code ?? ''} ${error.name ?? ''}`.toUpperCase()
  const message = `${error.message ?? ''} ${error.cause?.message ?? ''}`.toLowerCase()
  if (code.includes('ABORT') || message.includes('aborted') || code.includes('TIMEOUT')
    || code.includes('UND_ERR_CONNECT_TIMEOUT') || code.includes('UND_ERR_HEADERS_TIMEOUT')
    || code.includes('ETIMEDOUT')) {
    return 'timeout'
  }
  if (code.includes('ECONNREFUSED')) return 'proxy-refused'
  if (code.includes('ENOTFOUND') || code.includes('EAI_AGAIN')) return 'dns'
  if (message.includes('407') || message.includes('proxy authentication')) return 'proxy-auth'
  if (
    code.includes('CERT')
    || message.includes('ssl')
    || message.includes('tls')
    || message.includes('certificate')
  ) {
    return 'tls'
  }
  if (code.includes('ECONNRESET') || message.includes('tunnel') || code.includes('UND_ERR_SOCKET')) {
    return 'target-unreachable'
  }
  return 'unknown'
}

type ClosableDispatcher = { close(): Promise<void> }

type UndiciTestRuntime = {
  ProxyAgent: new (opts: string | { uri: string; connect?: { timeout: number } }) => ClosableDispatcher
  Socks5ProxyAgent: new (uri: string) => ClosableDispatcher
  request: (
    url: string,
    options: {
      method: string
      dispatcher: ClosableDispatcher
      signal: AbortSignal
      headersTimeout: number
      bodyTimeout: number
      maxRedirections: number
    },
  ) => Promise<{ statusCode: number; body: { dump(): Promise<void> } }>
}

function timeoutResult(): Promise<DesktopProxyTestResult> {
  return new Promise(resolve => {
    setTimeout(() => { resolve({ ok: false, code: 'timeout' }) }, TEST_TIMEOUT_MS)
  })
}

async function probeThroughProxy(
  undici: UndiciTestRuntime,
  parsed: string,
  kind: 'http' | 'socks',
): Promise<DesktopProxyTestResult> {
  const dispatcher = kind === 'socks'
    ? new undici.Socks5ProxyAgent(parsed)
    : new undici.ProxyAgent({ uri: parsed, connect: { timeout: TEST_TIMEOUT_MS } })
  const abort = new AbortController()
  const timer = setTimeout(() => {
    abort.abort()
    void dispatcher.close().catch(() => {})
  }, TEST_TIMEOUT_MS)
  try {
    const response = await undici.request(DESKTOP_MODEL_PROXY_TEST_ORIGIN, {
      method: 'GET',
      dispatcher,
      signal: abort.signal,
      headersTimeout: TEST_TIMEOUT_MS,
      bodyTimeout: TEST_TIMEOUT_MS,
      maxRedirections: 0,
    })
    await response.body.dump()
    if (response.statusCode === 407) return { ok: false, code: 'proxy-auth' }
    return { ok: true, code: 'ok' }
  } catch (cause) {
    return { ok: false, code: classifyDesktopProxyTestError(cause) }
  } finally {
    clearTimeout(timer)
    void dispatcher.close().catch(() => {})
  }
}

/**
 * Try to reach api.x.ai through the drafted proxy URL.
 * An empty URL uses HTTP_PROXY from the process environment.
 * The probe always settles within the timeout even if CONNECT hangs.
 * @param proxyUrl - settings draft, possibly empty.
 */
export async function testDesktopModelProxy(proxyUrl: string): Promise<DesktopProxyTestResult> {
  let parsed: string
  try {
    parsed = parseDesktopProxyUrl(proxyUrl)
  } catch {
    return { ok: false, code: 'invalid-url' }
  }
  if (parsed === '') parsed = environmentProxyUrl()
  if (parsed === '') return { ok: false, code: 'no-proxy' }
  const kind = desktopProxyKind(parsed)
  if (kind === 'none') return { ok: false, code: 'no-proxy' }

  const undici = await import('undici').catch(() => undefined) as UndiciTestRuntime | undefined
  if (undici?.request === undefined || undici.ProxyAgent === undefined) {
    return { ok: false, code: 'unknown' }
  }
  if (kind === 'socks' && undici.Socks5ProxyAgent === undefined) {
    return { ok: false, code: 'unknown' }
  }
  return await Promise.race([
    probeThroughProxy(undici, parsed, kind),
    timeoutResult(),
  ])
}
