import { request as requestHttps } from 'node:https'
import { describe, expect, it } from 'vitest'
import { startFakeDeepSeek } from './fake-deepseek-server.mjs'

describe('fake DeepSeek server', () => {
  it('returns a deterministic streaming model reply in two deltas', async () => {
    const server = await startFakeDeepSeek({ text: 'E2E_PONG' })

    try {
      const response = await postJson(new URL('/chat/completions', server.url), { messages: [] })
      expect(response.status).toBe(200)
      expect(response.headers['content-type']).toContain('text/event-stream')
      expect(response.body).toContain('E2E_')
      expect(response.body).toContain('PONG')
      expect(response.body).toContain('[DONE]')
      expect(server.requests()).toEqual([
        expect.objectContaining({ path: '/chat/completions', method: 'POST' }),
      ])
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
