/** Normalize the relay origin typed in the phone-connection modal. */

/**
 * Accept a bare host/IP by adding `https://`, then enforce the same origin
 * rules as the host: HTTPS off-box, or HTTP on loopback only.
 * @param value - raw input.
 * @returns canonical origin without a trailing slash, or null when invalid.
 */
export function normalizeRelayOrigin(value: string): string | null {
  const trimmed = value.trim()
  if (trimmed === '') return null
  const withScheme = /^[a-z][a-z0-9+.-]*:\/\//iu.test(trimmed) ? trimmed : `https://${trimmed}`
  try {
    const url = new URL(withScheme)
    if (url.protocol !== 'https:' && url.protocol !== 'http:') return null
    if (url.pathname !== '/' || url.search !== '' || url.hash !== '') return null
    if (url.username !== '' || url.password !== '') return null
    if (url.protocol === 'http:' && !/^(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$/u.test(url.host)) return null
    return url.origin
  } catch {
    return null
  }
}
