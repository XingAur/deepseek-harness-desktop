/** Validation for the configured remote-control relay origin. */

/**
 * Accept a configured relay origin: an `https://` origin on the public
 * internet, or an `http://` origin on loopback for local development.
 * Everything else — paths, queries, credentials, plain HTTP off-box — is
 * rejected so a typo can never widen what the tunnel trusts.
 * @param value - raw settings value.
 * @returns the canonical origin URL, or null for the disabled state.
 */
export function canonicalRelayOrigin(value: string): URL | null {
  if (value === '') return null
  let parsed: URL
  try {
    parsed = new URL(value)
  } catch {
    return null
  }
  if (parsed.protocol !== 'https:' && parsed.protocol !== 'http:') return null
  if (parsed.pathname !== '/' && parsed.pathname !== '') return null
  if (parsed.search !== '' || parsed.hash !== '') return null
  if (parsed.username !== '' || parsed.password !== '') return null
  if (parsed.protocol === 'http:' && !/^(?:localhost|127\.0\.0\.1|\[::1\])(?::\d+)?$/u.test(parsed.host)) return null
  return parsed
}

/** Persist relay origin into the active home's overlay-owned settings file. */
export async function persistRelayOrigin(home: string, value: string): Promise<URL | null> {
  const origin = canonicalRelayOrigin(value.trim())
  const { writeOverlayConfig } = await import('./overlay-config.ts')
  await writeOverlayConfig(home, { remoteRelayOrigin: origin === null ? '' : origin.origin })
  return origin
}
