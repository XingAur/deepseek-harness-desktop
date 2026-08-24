import { request as requestHttps } from 'node:https'
import { describe, expect, it } from 'vitest'
import { startFakeModelProvider } from './fake-model-provider.mjs'

describe('fake model provider', () => {
  it.each([
    ['deepseek', '/chat/completions'],
    ['openai', '/chat/completions'],
    ['compatible', '/chat/completions'],
    ['claude', '/v1/messages'],
  ] as const)('serves deterministic streaming fixtures for %s', async (family, path) => {
    const server = await startFakeModelProvider({ family, text: 'MODEL_OK' })
    try {
      const response = await postJson(new URL(path, server.url), { model: 'fixture', messages: [] })
      expect(response.status).toBe(200)
      expect(response.headers['content-type']).toContain('text/event-stream')
      expect(response.body).toContain('MODE')
      expect(response.body).toContain('L_OK')
      expect(server.requests()).toEqual([expect.objectContaining({ path, method: 'POST' })])
    } finally {
      await server.close()
    }
  })

  it('returns bounded provider error fixtures without requiring credentials', async () => {
    const server = await startFakeModelProvider({ family: 'openai', status: 429 })
    try {
      const response = await postJson(new URL('/chat/completions', server.url), {})
      expect(response.status).toBe(429)
      expect(response.body).toContain('rate limited')
    } finally {
      await server.close()
    }
  })
})

function postJson(url: URL, body: unknown) {
  return new Promise<{ status: number; headers: Record<string, string | string[] | undefined>; body: string }>((resolve, reject) => {
    const request = requestHttps(url, {
      method: 'POST',
      rejectUnauthorized: false,
      headers: { 'content-type': 'application/json' },
    }, (response) => {
      const chunks: Buffer[] = []
      response.on('data', (chunk) => chunks.push(Buffer.from(chunk)))
      response.on('end', () => resolve({
        status: response.statusCode ?? 0,
        headers: response.headers,
        body: Buffer.concat(chunks).toString(),
      }))
    })
    request.on('error', reject)
    request.end(JSON.stringify(body))
  })
}
