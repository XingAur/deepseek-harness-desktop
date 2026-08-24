import { createHash, randomBytes } from 'node:crypto'

export const CALLBACK_PATH = '/oauth/callback'

export interface OAuthClientConfig {
  authorizationEndpoint: string
  clientId: string
  scopes: string[]
}

export interface OAuthCallback {
  path: string
  state: string
  code: string
}

export interface OAuthExpectedCallback {
  state: string
  codeVerifier: string
  consumed?: boolean
}

export interface OAuthTokenSet {
  accessToken: string
  refreshToken?: string
  expiresIn?: number
}

export interface OAuthTokenSink {
  put(secret: string): Promise<string>
}

export class OAuthError extends Error {
  readonly code: string

  constructor(code: string, message: string) {
    super(message)
    this.name = 'OAuthError'
    this.code = code
  }
}

export function createOAuthState(seed?: string): string {
  const input = seed ?? randomBytes(32).toString('base64url')
  return createHash('sha256').update(input).digest('base64url')
}

export function buildAuthorizationUrl(
  config: OAuthClientConfig,
  options: { port: number; state: string; codeVerifier: string },
): URL {
  const endpoint = validateHttpsEndpoint(config.authorizationEndpoint)
  if (!Number.isInteger(options.port) || options.port < 1 || options.port > 65_535) throw new OAuthError('oauth-port-invalid', 'OAuth 回调端口无效')
  const url = new URL(endpoint)
  url.searchParams.set('response_type', 'code')
  url.searchParams.set('client_id', config.clientId)
  url.searchParams.set('redirect_uri', `http://127.0.0.1:${options.port}${CALLBACK_PATH}`)
  url.searchParams.set('state', options.state)
  url.searchParams.set('code_challenge', pkceChallenge(options.codeVerifier))
  url.searchParams.set('code_challenge_method', 'S256')
  url.searchParams.set('scope', config.scopes.join(' '))
  return url
}

export async function exchangeCallback(
  callback: OAuthCallback,
  expected: OAuthExpectedCallback,
  sink: OAuthTokenSink,
  tokens: OAuthTokenSet,
): Promise<{ credentialId: string }> {
  if (expected.consumed) throw new OAuthError('oauth-callback-used', 'OAuth 回调已经使用')
  if (callback.path !== CALLBACK_PATH) throw new OAuthError('oauth-callback-path-mismatch', 'OAuth 回调路径不匹配')
  if (callback.state !== expected.state) throw new OAuthError('oauth-state-mismatch', 'OAuth state 校验失败')
  if (callback.code.trim() === '' || callback.code.length > 4096) throw new OAuthError('oauth-code-invalid', 'OAuth 授权码无效')
  assertToken(tokens.accessToken, 'OAuth access token')
  if (tokens.refreshToken !== undefined) assertToken(tokens.refreshToken, 'OAuth refresh token')
  const secret = JSON.stringify({ accessToken: tokens.accessToken, ...(tokens.refreshToken === undefined ? {} : { refreshToken: tokens.refreshToken }), ...(tokens.expiresIn === undefined ? {} : { expiresIn: tokens.expiresIn }) })
  return { credentialId: await sink.put(secret) }
}

export function validateIssuerUrl(value: string): string {
  let url: URL
  try { url = new URL(value) } catch { throw new OAuthError('oauth-issuer-rejected', 'OAuth issuer 地址无效') }
  const loopback = ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)
  if (url.username !== '' || url.password !== '' || url.hash !== '' || (url.protocol !== 'https:' && !(loopback && url.protocol === 'http:'))) {
    throw new OAuthError('oauth-issuer-rejected', 'OAuth issuer 必须使用 HTTPS 或回环地址')
  }
  return url.toString()
}

function validateHttpsEndpoint(value: string): string {
  let url: URL
  try { url = new URL(value) } catch { throw new OAuthError('oauth-endpoint-rejected', 'OAuth 授权地址无效') }
  if (url.protocol !== 'https:' || url.username !== '' || url.password !== '' || url.hash !== '') throw new OAuthError('oauth-endpoint-rejected', 'OAuth 授权地址必须使用 HTTPS')
  return url.toString()
}

function pkceChallenge(verifier: string): string {
  if (verifier.length < 43 || verifier.length > 128) throw new OAuthError('oauth-verifier-invalid', 'OAuth PKCE verifier 无效')
  return createHash('sha256').update(verifier).digest('base64url')
}

function assertToken(value: string, label: string): void {
  if (value.length === 0 || value.length > 16 * 1024) throw new OAuthError('oauth-token-invalid', `${label} 无效`)
}
