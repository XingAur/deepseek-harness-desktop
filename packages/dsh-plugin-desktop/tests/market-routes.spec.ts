import type { IncomingMessage, ServerResponse } from 'node:http'
import { describe, expect, it, vi } from 'vitest'
import { safeWrite } from '../src/market-routes'

const PORT = 4300

function request(headers: Record<string, string>, method = 'POST'): IncomingMessage {
  return { method, headers } as unknown as IncomingMessage
}

function response(): { res: ServerResponse; writeHead: ReturnType<typeof vi.fn> } {
  const writeHead = vi.fn()
  const end = vi.fn()
  return { res: { writeHead, end } as unknown as ServerResponse, writeHead }
}

describe('safeWrite 同源写保护', () => {
  it('接受同源 JSON POST', () => {
    const { res, writeHead } = response()
    const ok = safeWrite(request({ 'content-type': 'application/json', origin: `http://127.0.0.1:${PORT}`, host: `127.0.0.1:${PORT}` }), res)
    expect(ok).toBe(true)
    expect(writeHead).not.toHaveBeenCalled()
  })

  it('拒绝缺少 Origin 的请求', () => {
    const { res, writeHead } = response()
    expect(safeWrite(request({ 'content-type': 'application/json', host: `127.0.0.1:${PORT}` }), res)).toBe(false)
    expect(writeHead).toHaveBeenCalledWith(403, expect.anything())
  })

  it('拒绝非回环、非同端口或 HTTPS 来源', () => {
    for (const origin of [`https://127.0.0.1:${PORT}`, 'http://localhost:4300', 'http://127.0.0.1:9999', 'http://evil.example']) {
      const { res, writeHead } = response()
      expect(safeWrite(request({ 'content-type': 'application/json', origin, host: `127.0.0.1:${PORT}` }), res)).toBe(false)
      expect(writeHead).toHaveBeenCalledWith(403, expect.anything())
    }
  })

  it('拒绝非 POST 或非 JSON 请求', () => {
    expect(safeWrite(request({ origin: `http://127.0.0.1:${PORT}`, 'content-type': 'application/json' }, 'GET'), response().res)).toBe(false)
    expect(safeWrite(request({ origin: `http://127.0.0.1:${PORT}`, 'content-type': 'text/plain' }), response().res)).toBe(false)
  })
})
