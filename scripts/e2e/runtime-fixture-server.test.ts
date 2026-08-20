import { createPublicKey, verify } from 'node:crypto'
import { request as requestHttps } from 'node:https'
import { describe, expect, it } from 'vitest'
import { canonicalJson } from '../canonical-json.mjs'
import { startRuntimeFixture } from './runtime-fixture-server.mjs'

describe('runtime fixture server', () => {
  it('serves resumable signed runtime bytes and records requests', async () => {
    const fixture = await startRuntimeFixture({
      archive: Buffer.from('0123456789'),
      signature: 'fixture-signature',
    })

    try {
      const response = await fixtureRequest(new URL('/runtime.zip', fixture.url), {
        range: 'bytes=5-',
      })

      expect(response.status).toBe(206)
      expect(response.headers['content-range']).toBe('bytes 5-9/10')
      expect(response.body.toString()).toBe('56789')
      expect(fixture.requests()).toEqual(expect.arrayContaining([
        expect.objectContaining({ path: '/runtime.zip', range: 'bytes=5-' }),
      ]))
    } finally {
      await fixture.close()
    }
  })

  it('switches failure scenarios without restarting the server', async () => {
    const fixture = await startRuntimeFixture({ archive: Buffer.from('trusted') })

    try {
      fixture.setScenario('tampered-archive')
      const tampered = await fixtureRequest(new URL('/runtime.zip', fixture.url))
      expect(tampered.body).not.toEqual(Buffer.from('trusted'))

      fixture.setScenario('wrong-target')
      const manifest = await fixtureRequest(new URL('/manifest.json', fixture.url))
      const document = JSON.parse(manifest.body.toString())
      expect(document.target).toBe('linux-x86_64')
      const signature = document.signature
      delete document.signature
      const key = createPublicKey({
        key: { kty: 'OKP', crv: 'Ed25519', x: fixture.publicKey },
        format: 'jwk',
      })
      expect(verify(null, Buffer.from(canonicalJson(document)), key, Buffer.from(signature, 'base64url'))).toBe(true)

      fixture.clearRequests()
      expect(fixture.requests()).toEqual([])
    } finally {
      await fixture.close()
    }
  })

  it('delays deterministically and resumes from a range after one disconnect', async () => {
    const fixture = await startRuntimeFixture({ archive: Buffer.from('0123456789'), delayMs: 50 })

    try {
      fixture.setScenario('delayed')
      const startedAt = performance.now()
      await fixtureRequest(new URL('/runtime.zip', fixture.url))
      expect(performance.now() - startedAt).toBeGreaterThanOrEqual(40)

      fixture.setScenario('disconnect-once')
      await expect(fixtureRequest(new URL('/runtime.zip', fixture.url))).rejects.toThrow()
      const resumed = await fixtureRequest(new URL('/runtime.zip', fixture.url), { range: 'bytes=5-' })
      expect(resumed.status).toBe(206)
      expect(resumed.body.toString()).toBe('56789')
    } finally {
      await fixture.close()
    }
  })
})

function fixtureRequest(url: URL, headers: Record<string, string> = {}) {
  return new Promise<{ status: number; headers: Record<string, string | string[] | undefined>; body: Buffer }>((resolve, reject) => {
    const request = requestHttps(url, { headers, rejectUnauthorized: false }, (response) => {
      const chunks: Buffer[] = []
      response.on('data', (chunk) => chunks.push(Buffer.from(chunk)))
      response.on('end', () => resolve({
        status: response.statusCode ?? 0,
        headers: response.headers,
        body: Buffer.concat(chunks),
      }))
    })
    request.on('error', reject)
    request.end()
  })
}
