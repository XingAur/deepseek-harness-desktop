import http from 'node:http'
import { AddressInfo } from 'node:net'
import { describe, expect, it } from 'vitest'
import { probe } from './port-probe.js'

function listen(handler: http.RequestListener): Promise<http.Server> {
  return new Promise(resolve => {
    const s = http.createServer(handler)
    s.listen(0, '127.0.0.1', () => resolve(s))
  })
}
const portOf = (s: http.Server) => (s.address() as AddressInfo).port

describe('probe', () => {
  it('无人监听 → none', async () => {
    const s = await listen((_q, res) => res.end())
    const port = portOf(s); s.close()
    await new Promise(r => setTimeout(r, 100))
    expect(await probe(port, 500)).toBe('none')
  })
  it('200 + text/html → dsh', async () => {
    const s = await listen((_q, res) => { res.setHeader('content-type', 'text/html'); res.end('<html></html>') })
    expect(await probe(portOf(s), 500)).toBe('dsh')
    s.close()
  })
  it('200 + json → foreign', async () => {
    const s = await listen((_q, res) => { res.setHeader('content-type', 'application/json'); res.end('{}') })
    expect(await probe(portOf(s), 500)).toBe('foreign')
    s.close()
  })
})
