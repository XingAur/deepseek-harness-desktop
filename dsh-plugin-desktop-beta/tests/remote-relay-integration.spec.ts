/** End-to-end relay protocol coverage: embedded relay, real tunnel, fixture upstream. */

import { createServer, type Server } from 'node:http'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import WebSocket, { WebSocketServer } from 'ws'
import { createRelayApp } from '../../relay-server/main.mjs'
import { RemoteRelayTunnel } from '../src/remote-relay-tunnel.ts'

const PAIR = 'abcdefghij'
const SECRET = 's'.repeat(43)

function listen(server: Server): Promise<number> {
  return new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (address === null || typeof address === 'string') {
        reject(new Error('relay test server did not expose a port'))
        return
      }
      resolve(address.port)
    })
  })
}

function waitFor(predicate: () => boolean, label: string, timeoutMs = 5_000): Promise<void> {
  return new Promise((resolve, reject) => {
    const started = Date.now()
    const timer = setInterval(() => {
      if (predicate()) { clearInterval(timer); resolve(); return }
      if (Date.now() - started > timeoutMs) {
        clearInterval(timer)
        reject(new Error(`timed out waiting for ${label}`))
      }
    }, 20)
  })
}

describe('remote relay tunnel integration', () => {
  let upstream: Server
  let upstreamPort = 0
  let relay: ReturnType<typeof createRelayApp>
  let relayPort = 0
  const seenRendererHeader: string[] = []

  beforeAll(async () => {
    upstream = createServer((req, res) => {
      if (req.url === '/echo' && req.method === 'GET') {
        res.writeHead(200, { 'content-type': 'application/json', 'x-upstream': 'yes' })
        res.end('{"ok":true}')
        return
      }
      if (req.url === '/upper' && req.method === 'POST') {
        seenRendererHeader.push(String(req.headers['x-dsh-desktop-renderer'] ?? 'absent'))
        const chunks: Buffer[] = []
        req.on('data', chunk => { chunks.push(chunk as Buffer) })
        req.on('end', () => {
          res.writeHead(200, { 'content-type': 'text/plain' })
          res.end(Buffer.concat(chunks).toString('utf8').toUpperCase())
        })
        return
      }
      res.writeHead(404).end()
    })
    upstreamPort = await listen(upstream)

    relay = createRelayApp({ WebSocketServer, WebSocket })
    relayPort = await listen(relay.httpServer)
  })

  afterAll(async () => {
    relay.stop()
    await new Promise<void>(resolve => { upstream.close(() => { resolve() }) })
  })

  it('relays a tunneled GET end to end', async () => {
    const tunnel = new RemoteRelayTunnel({
      relayUrl: `ws://127.0.0.1:${String(relayPort)}/host`,
      pairId: PAIR,
      secret: SECRET,
      targetPort: upstreamPort,
    })
    tunnel.start()
    try {
      await waitFor(() => tunnel.snapshot().state === 'ready', 'tunnel ready')
      const response = await fetch(`http://127.0.0.1:${String(relayPort)}/r/${PAIR}/echo`)
      expect(response.status).toBe(200)
      expect(response.headers.get('x-upstream')).toBe('yes')
      expect(await response.json()).toEqual({ ok: true })
    } finally {
      tunnel.dispose()
    }
  })

  it('relays a tunneled POST body without leaking the renderer header', async () => {
    const tunnel = new RemoteRelayTunnel({
      relayUrl: `ws://127.0.0.1:${String(relayPort)}/host`,
      pairId: PAIR,
      secret: SECRET,
      targetPort: upstreamPort,
    })
    tunnel.start()
    try {
      await waitFor(() => tunnel.snapshot().state === 'ready', 'tunnel ready')
      const response = await fetch(`http://127.0.0.1:${String(relayPort)}/r/${PAIR}/upper`, {
        method: 'POST',
        headers: { 'x-dsh-desktop-renderer': 'must-not-arrive', 'content-type': 'text/plain' },
        body: 'hello relay',
      })
      expect(response.status).toBe(200)
      expect(await response.text()).toBe('HELLO RELAY')
      expect(seenRendererHeader.at(-1)).toBe('absent')
    } finally {
      tunnel.dispose()
    }
  })

  it('answers 503 while the desktop tunnel is offline', async () => {
    const response = await fetch(`http://127.0.0.1:${String(relayPort)}/r/${PAIR}/echo`)
    expect(response.status).toBe(503)
  })
})
