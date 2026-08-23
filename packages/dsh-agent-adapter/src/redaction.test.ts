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
})
