import { describe, expect, it } from 'vitest'
import { MAX_DIAGNOSTIC_TEXT_BYTES, redactDiagnostic } from './redaction.js'

describe('redactDiagnostic', () => {
  it('removes credential-bearing headers, tokens, cookies, private keys, and secret-shaped environment values', () => {
    const diagnostic = [
      'Authorization: Bearer top-secret-token',
      'x-api-key: api-key-value',
      'oauth_token=oauth-value',
      'Cookie: session=private-cookie',
      '-----BEGIN PRIVATE KEY-----\nprivate-key-material\n-----END PRIVATE KEY-----',
      'DATABASE_PASSWORD=database-secret',
    ].join('\n')

    const redacted = redactDiagnostic(diagnostic)

    for (const secret of ['top-secret-token', 'api-key-value', 'oauth-value', 'private-cookie', 'private-key-material', 'database-secret']) {
      expect(redacted).not.toContain(secret)
    }
    expect(redacted).toContain('[REDACTED]')
  })

  it('bounds UTF-8 diagnostic output while preserving redaction', () => {
    const redacted = redactDiagnostic(`Authorization: Bearer hidden\n${'中'.repeat(MAX_DIAGNOSTIC_TEXT_BYTES)}`)

    expect(Buffer.byteLength(redacted, 'utf8')).toBeLessThanOrEqual(MAX_DIAGNOSTIC_TEXT_BYTES)
    expect(redacted).not.toContain('hidden')
  })

  it('redacts quoted JSON credentials, assignment forms, and cookie variants before UTF-8 truncation', () => {
    const secrets = ['json-token', 'json-key', 'quoted-password', 'bearer-secret', 'cookie-secret', 'set-cookie-secret', 'access-secret']
    const diagnostic = [
      '{"token":"json-token","apiKey":"json-key","password":"quoted-password"}',
      'Authorization=Bearer bearer-secret',
      'cookie=session=cookie-secret',
      'Set-Cookie: auth=set-cookie-secret; HttpOnly',
      'access_token: access-secret',
      '中'.repeat(MAX_DIAGNOSTIC_TEXT_BYTES),
    ].join('\n')

    const redacted = redactDiagnostic(diagnostic)

    for (const secret of secrets) expect(redacted).not.toContain(secret)
    expect(redacted).toContain('[REDACTED]')
    expect(Buffer.byteLength(redacted, 'utf8')).toBeLessThanOrEqual(MAX_DIAGNOSTIC_TEXT_BYTES)
  })

  it('redacts client secrets, folded headers, embedded JSON headers, and URL query credentials', () => {
    const secrets = ['client-secret', 'camel-secret', 'cookie-secret', 'auth-secret', 'query-secret', 'password-secret']
    const redacted = redactDiagnostic([
      'client_secret=client-secret', 'clientSecret: camel-secret', '{"set-cookie":"cookie-secret","Authorization":"Bearer auth-secret"}',
      'Authorization: Bearer folded\r\n query-secret', 'https://host/?api_key=query-secret&password=password-secret',
      '中'.repeat(MAX_DIAGNOSTIC_TEXT_BYTES),
    ].join('\n'))
    for (const secret of secrets) expect(redacted).not.toContain(secret)
    expect(Buffer.byteLength(redacted, 'utf8')).toBeLessThanOrEqual(MAX_DIAGNOSTIC_TEXT_BYTES)
  })
})
