import { describe, expect, it } from 'vitest'
import {
  CALLBACK_PATH,
  buildAuthorizationUrl,
  createOAuthState,
  exchangeCallback,
  validateIssuerUrl,
  type OAuthTokenSink,
} from './oauth.js'

describe('MCP OAuth helpers', () => {
  it('builds loopback PKCE authorization URLs without secrets', () => {
    const state = createOAuthState('fixed-test-seed')
    const url = buildAuthorizationUrl({
      authorizationEndpoint: 'https://mcp.example.com/authorize',
      clientId: 'desktop-client',
      scopes: ['tools.read'],
    }, { port: 43123, state, codeVerifier: 'v'.repeat(43) })
    expect(url.pathname).toBe('/authorize')
    expect(url.searchParams.get('redirect_uri')).toBe(`http://127.0.0.1:43123${CALLBACK_PATH}`)
    expect(url.searchParams.get('state')).toBe(state)
    expect(url.searchParams.get('code_verifier')).toBeNull()
    expect(url.toString()).not.toContain('token')
  })

  it('accepts a callback once and stores tokens only through the credential sink', async () => {
    const state = createOAuthState('fixed-test-seed')
    const stored: string[] = []
    const sink: OAuthTokenSink = { put: async (secret) => { stored.push(secret); return 'credential-oauth-1' } }
    const result = await exchangeCallback({ path: CALLBACK_PATH, state, code: 'authorization-code' }, { state, codeVerifier: 'v'.repeat(43) }, sink, { accessToken: 'access-token', refreshToken: 'refresh-token' })
    expect(result.credentialId).toBe('credential-oauth-1')
    expect(stored).toHaveLength(1)
    expect(stored[0]).toContain('access-token')
    await expect(exchangeCallback({ path: CALLBACK_PATH, state, code: 'authorization-code' }, { state, codeVerifier: 'v'.repeat(43), consumed: true }, sink, { accessToken: 'access-token' })).rejects.toMatchObject({ code: 'oauth-callback-used' })
  })

  it('rejects state/path mismatch and non-HTTPS issuers outside loopback', async () => {
    const state = createOAuthState('fixed-test-seed')
    const sink: OAuthTokenSink = { put: async () => 'credential-oauth-1' }
    await expect(exchangeCallback({ path: CALLBACK_PATH, state: 'wrong', code: 'code' }, { state, codeVerifier: 'v'.repeat(43) }, sink, { accessToken: 'token' })).rejects.toMatchObject({ code: 'oauth-state-mismatch' })
    await expect(exchangeCallback({ path: '/wrong', state, code: 'code' }, { state, codeVerifier: 'v'.repeat(43) }, sink, { accessToken: 'token' })).rejects.toMatchObject({ code: 'oauth-callback-path-mismatch' })
    expect(() => validateIssuerUrl('http://mcp.example.com')).toThrow('OAuth issuer 必须使用 HTTPS')
    expect(validateIssuerUrl('http://127.0.0.1:43123')).toBe('http://127.0.0.1:43123/')
  })
})
