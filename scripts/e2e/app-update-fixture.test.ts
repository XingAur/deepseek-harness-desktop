import { request as requestHttps } from 'node:https'
import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { startAppUpdateFixture } from './app-update-fixture.mjs'

describe('application update fixture', () => {
  it('publishes updater metadata and immutable candidate bytes', async () => {
    const fixture = await startAppUpdateFixture()

    try {
      await fixture.publish('9.9.9', Buffer.from('desktop-candidate'))
      const metadata = await fixtureRequest(new URL('/latest.json', fixture.endpoint))
      const document = JSON.parse(metadata.body.toString())
      const candidate = await fixtureRequest(new URL(document.platforms['windows-x86_64'].url))

      expect(document.version).toBe('9.9.9')
      expect(document.platforms['windows-x86_64'].signature).toMatch(/^fixture-sha256:/)
      expect(candidate.body.toString()).toBe('desktop-candidate')
      expect(fixture.requests().map((request) => request.path)).toEqual([
        '/latest.json',
        '/downloads/9.9.9.exe',
      ])
    } finally {
      await fixture.close()
    }
  })

  it('refuses to replace an already published version with different bytes', async () => {
    const fixture = await startAppUpdateFixture()

    try {
      await fixture.publish('9.9.9', Buffer.from('first'))
      await expect(fixture.publish('9.9.9', Buffer.from('second'))).rejects.toThrow('内容不同')
    } finally {
      await fixture.close()
    }
  })

  it('passes the updater password through the child environment, not the process list', () => {
    const source = readFileSync('scripts/e2e/app-update-fixture.mjs', 'utf8')
    expect(source).toContain('TAURI_SIGNING_PRIVATE_KEY_PASSWORD: options.password')
    expect(source).not.toContain("'--password'")
  })
})

function fixtureRequest(url: URL) {
  return new Promise<{ status: number; body: Buffer }>((resolve, reject) => {
    const request = requestHttps(url, { rejectUnauthorized: false }, (response) => {
      const chunks: Buffer[] = []
      response.on('data', (chunk) => chunks.push(Buffer.from(chunk)))
      response.on('end', () => resolve({ status: response.statusCode ?? 0, body: Buffer.concat(chunks) }))
    })
    request.on('error', reject)
    request.end()
  })
}
