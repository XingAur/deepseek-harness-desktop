/** Parse the Desktop-owned outbound proxy URL without installing transport. */

const BIN_NAME = 'dsh-plugin-desktop'
const MAX_PROXY_URL_BYTES = 2048
const HTTP_PROTOCOLS = new Set(['http:', 'https:'])
const SOCKS_PROTOCOLS = new Set(['socks:', 'socks5:', 'socks5h:'])

/** Maximum accepted length of a persisted proxy URL. */
export const DESKTOP_PROXY_URL_MAX_BYTES = MAX_PROXY_URL_BYTES

/** Kind of outbound proxy named by a Desktop setting. */
export type DesktopProxyKind = 'http' | 'socks' | 'none'

/**
 * Parse a Desktop settings proxy URL.
 * @param value - untrusted settings value.
 * @returns a trimmed URL, or an empty string when the setting is unset.
 */
export function parseDesktopProxyUrl(value: unknown): string {
  if (value === undefined || value === '') return ''
  if (typeof value !== 'string') {
    throw new Error(`${BIN_NAME}: dsh-desktop.proxyUrl must be a string`)
  }
  const trimmed = value.trim()
  if (trimmed === '') return ''
  if (trimmed.length > MAX_PROXY_URL_BYTES) {
    throw new Error(`${BIN_NAME}: dsh-desktop.proxyUrl is longer than ${String(MAX_PROXY_URL_BYTES)} characters`)
  }
  let parsed: URL
  try {
    parsed = new URL(trimmed)
  } catch {
    throw new Error(`${BIN_NAME}: dsh-desktop.proxyUrl must be an http://, https://, or socks5:// URL`)
  }
  if (HTTP_PROTOCOLS.has(parsed.protocol) || SOCKS_PROTOCOLS.has(parsed.protocol)) return trimmed
  throw new Error(`${BIN_NAME}: dsh-desktop.proxyUrl uses unsupported scheme ${parsed.protocol}`)
}

/**
 * Classify a parsed Desktop proxy URL.
 * @param value - already-validated proxy URL or empty string.
 */
export function desktopProxyKind(value: string): DesktopProxyKind {
  if (value === '') return 'none'
  const protocol = new URL(value).protocol
  if (SOCKS_PROTOCOLS.has(protocol)) return 'socks'
  return 'http'
}

/**
 * Return a credential-free origin for diagnostics.
 * @param value - a parseable proxy URL.
 */
export function desktopProxyDiagnosticOrigin(value: string): string {
  const parsed = new URL(value)
  parsed.username = ''
  parsed.password = ''
  return `${parsed.protocol}//${parsed.host}`
}
