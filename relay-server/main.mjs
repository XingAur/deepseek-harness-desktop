#!/usr/bin/env node
/** Cloud relay for DSH Desktop remote control: phone HTTP/WS in, host tunnel out. */

import { createServer } from 'node:http'
import { randomBytes } from 'node:crypto'

/** Limits: one pairing is one desktop; keep the relay cheap and forgetful. */
const MAX_PAIR_TTL_MS = 10 * 60 * 1000
const MAX_PENDING = 64
const MAX_BODY_BYTES = 8 * 1024 * 1024
const IDLE_PING_MS = 30_000
const PAIR_PATTERN = /^[a-z2-7]{10}$/u
const SECRET_PATTERN = /^[A-Za-z0-9_-]{43}$/u
const PAIR_PREFIX = /^\/r\/([a-z2-7]{10})(\/.*)?$/u

/** One claimed pairing: the authenticated host tunnel plus its live requests. */
class Pairing {
  constructor(id, socket, webSocketOpen) {
    this.id = id
    this.socket = socket
    this.webSocketOpen = webSocketOpen
    this.pending = new Map()
    this.claimedAt = Date.now()
  }

  send(frame) {
    if (this.socket.readyState === this.webSocketOpen) this.socket.send(JSON.stringify(frame))
  }
}

function base64(body) {
  return Buffer.from(body).toString('base64')
}

function deny(res, status, message) {
  if (res.headersSent) { res.destroy(); return }
  res.writeHead(status, { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' })
  res.end(message)
}

function rewrittenLocation(value, pair) {
  if (typeof value !== 'string' || !value.startsWith('/')) return value
  if (value.startsWith(`/r/${pair}/`)) return value
  return `/r/${pair}${value}`
}

/**
 * Build the complete relay application. `WebSocketServer` and the WebSocket
 * namespace are injected so the desktop integration tests can embed this
 * server against the workspace's own `ws` copy without a second install.
 */
export function createRelayApp({ WebSocketServer, WebSocket }) {
  const pairings = new Map()

  /** Forward one phone HTTP request to the host tunnel and stream the answer back. */
  function tunnelRequest(pairing, req, res) {
    if (pairing.pending.size >= MAX_PENDING) {
      deny(res, 503, 'relay pairing is saturated')
      return
    }
    const id = randomBytes(8).toString('hex')
    const match = PAIR_PREFIX.exec(req.url)
    let forwarded = 0
    res.on('close', () => {
      if (pairing.pending.delete(id)) pairing.send({ t: 'abort', id })
    })
    req.on('data', chunk => {
      forwarded += chunk.length
      if (forwarded > MAX_BODY_BYTES) {
        deny(res, 413, 'request body exceeds the relay limit')
        req.destroy()
        return
      }
      pairing.send({ t: 'body', id, d: base64(chunk) })
    })
    req.on('end', () => pairing.send({ t: 'end', id }))
    req.on('error', () => res.destroy())
    pairing.pending.set(id, { kind: 'request', res })
    pairing.send({
      t: 'req',
      id,
      method: req.method ?? 'GET',
      path: match?.[2] ?? '/',
      headers: Object.entries(req.headers),
    })
  }

  function responseHeaders(pairing, frame) {
    const headers = {}
    for (const entry of Array.isArray(frame.headers) ? frame.headers : []) {
      if (!Array.isArray(entry) || typeof entry[0] !== 'string') continue
      const name = entry[0]
      const value = typeof entry[1] === 'string' ? entry[1] : Array.isArray(entry[1]) ? entry[1].join(', ') : null
      if (value === null) continue
      const next = name.toLowerCase() === 'location' ? rewrittenLocation(value, pairing.id) : value
      if (name.toLowerCase() === 'set-cookie') {
        const current = headers[name]
        headers[name] = current === undefined ? [next] : [...(Array.isArray(current) ? current : [current]), next]
      } else {
        headers[name] = next
      }
    }
    return headers
  }

  /** Apply one host-tunnel answer frame to its pending phone request or upgrade. */
  function hostFrame(pairing, frame) {
    if (frame.t === 'res') {
      const entry = pairing.pending.get(frame.id)
      if (entry?.kind !== 'request' || entry.res.headersSent) return
      try {
        entry.res.writeHead(frame.status ?? 502, responseHeaders(pairing, frame))
      } catch {
        deny(entry.res, 502, 'relay could not write the desktop response')
      }
      return
    }
    if (frame.t === 'body') {
      const entry = pairing.pending.get(frame.id)
      if (entry?.kind === 'request' && !entry.res.destroyed) entry.res.write(Buffer.from(frame.d ?? '', 'base64'))
      return
    }
    if (frame.t === 'fin' || frame.t === 'fail') {
      const entry = pairing.pending.get(frame.id)
      if (entry?.kind === 'request') {
        if (frame.t === 'fail' && !entry.res.headersSent) deny(entry.res, frame.status ?? 502, 'desktop tunnel failed')
        else entry.res.end()
      }
      pairing.pending.delete(frame.id)
      return
    }
    if (frame.t === 'upok') {
      const entry = pairing.pending.get(frame.id)
      if (entry?.kind !== 'upgrade') return
      entry.socket.write('HTTP/1.1 101 Switching Protocols\r\n')
      for (const [name, value] of Array.isArray(frame.headers) ? frame.headers : []) {
        entry.socket.write(`${name}: ${value}\r\n`)
      }
      entry.socket.write('\r\n')
      entry.socket.resume()
      return
    }
    if (frame.t === 'upfail') {
      const entry = pairing.pending.get(frame.id)
      if (entry?.kind === 'upgrade') {
        entry.socket.write(`HTTP/1.1 ${String(frame.status ?? 502)} tunnel unavailable\r\nconnection: close\r\n\r\n`)
        entry.socket.destroy()
        pairing.pending.delete(frame.id)
      }
      return
    }
    if (frame.t === 'ud') {
      const entry = pairing.pending.get(frame.id)
      if (entry?.kind === 'upgrade' && !entry.socket.destroyed) {
        entry.socket.write(Buffer.from(frame.d ?? '', 'base64'))
      }
      return
    }
    if (frame.t === 'uc') {
      const entry = pairing.pending.get(frame.id)
      if (entry?.kind === 'upgrade') entry.socket.destroy()
      pairing.pending.delete(frame.id)
    }
  }

  const hostServer = new WebSocketServer({ noServer: true })

  const httpServer = createServer((req, res) => {
    const url = req.url ?? '/'
    if (url === '/' || url === '/healthz') {
      res.writeHead(200, { 'content-type': 'text/plain; charset=utf-8', 'cache-control': 'no-store' })
      res.end('dsh-desktop relay\n')
      return
    }
    const match = PAIR_PREFIX.exec(url)
    if (match === null) {
      deny(res, 404, 'unknown relay path')
      return
    }
    const pairing = pairings.get(match[1])
    if (pairing === undefined) {
      deny(res, 503, 'desktop is offline; regenerate the pairing from the desktop app')
      return
    }
    tunnelRequest(pairing, req, res)
  })

  httpServer.on('upgrade', (req, socket, head) => {
    const url = req.url ?? ''
    if (url === '/host') {
      hostServer.handleUpgrade(req, socket, head, ws => {
        let pairing = null
        let authed = false
        ws.on('message', data => {
          let frame
          try {
            frame = JSON.parse(String(data))
          } catch {
            ws.close(4003, 'malformed frame')
            return
          }
          if (!authed) {
            if (frame.t !== 'hello' || typeof frame.pair !== 'string' || !PAIR_PATTERN.test(frame.pair)
              || typeof frame.secret !== 'string' || !SECRET_PATTERN.test(frame.secret)) {
              ws.close(4001, 'bad hello')
              return
            }
            const existing = pairings.get(frame.pair)
            if (existing !== undefined) existing.socket.close(4002, 'superseded')
            pairing = new Pairing(frame.pair, ws, WebSocket.OPEN)
            pairings.set(frame.pair, pairing)
            authed = true
            ws.send(JSON.stringify({ t: 'ready', pair: frame.pair }))
            return
          }
          try {
            hostFrame(pairing, frame)
          } catch {
            // A bad frame must not take the whole relay down.
          }
        })
        const drop = () => {
          if (pairing === null) return
          for (const entry of pairing.pending.values()) {
            if (entry.kind === 'request') {
              if (!entry.res.headersSent) deny(entry.res, 503, 'desktop tunnel disconnected')
              else entry.res.destroy()
            } else {
              entry.socket.destroy()
            }
          }
          if (pairings.get(pairing.id) === pairing) pairings.delete(pairing.id)
        }
        ws.on('close', drop)
        ws.on('error', drop)
      })
      return
    }
    const match = PAIR_PREFIX.exec(url)
    if (match === null) {
      socket.write('HTTP/1.1 404 Not Found\r\nconnection: close\r\n\r\n')
      socket.destroy()
      return
    }
    const pairing = pairings.get(match[1])
    if (pairing === undefined || pairing.pending.size >= MAX_PENDING) {
      socket.write('HTTP/1.1 503 Service Unavailable\r\nconnection: close\r\n\r\n')
      socket.destroy()
      return
    }
    const id = randomBytes(8).toString('hex')
    socket.pause()
    pairing.pending.set(id, { kind: 'upgrade', socket })
    socket.on('data', chunk => pairing.send({ t: 'ud', id, d: base64(chunk) }))
    socket.on('error', () => { pairing.pending.delete(id); pairing.send({ t: 'uc', id }) })
    socket.on('close', () => { pairing.pending.delete(id); pairing.send({ t: 'uc', id }) })
    pairing.send({
      t: 'upg',
      id,
      method: req.method ?? 'GET',
      path: match[2] ?? '/',
      headers: Object.entries(req.headers),
    })
    if (head?.length > 0) pairing.send({ t: 'ud', id, d: base64(head) })
  })

  const sweeper = setInterval(() => {
    for (const [id, pairing] of pairings) {
      if (Date.now() - pairing.claimedAt > MAX_PAIR_TTL_MS && pairing.pending.size === 0) {
        pairing.socket.terminate()
        pairings.delete(id)
      }
    }
  }, 60_000)
  const pinger = setInterval(() => {
    for (const pairing of pairings.values()) pairing.socket.ping()
  }, IDLE_PING_MS)

  return {
    httpServer,
    stop: () => {
      clearInterval(sweeper)
      clearInterval(pinger)
      for (const pairing of pairings.values()) pairing.socket.close(1001, 'relay stopping')
      pairings.clear()
      httpServer.closeIdleConnections()
      httpServer.closeAllConnections()
    },
    pairings,
  }
}

const isMain = process.argv[1] !== undefined && (
  import.meta.url === new URL(`file:///${process.argv[1].replace(/\\/gu, '/')}`).href
    || import.meta.url.endsWith(process.argv[1].replace(/\\/gu, '/').replace(/^\//u, ''))
)

if (isMain) {
  const args = process.argv.slice(2)
  const flag = (name, fallback) => {
    const index = args.indexOf(name)
    return index === -1 ? fallback : args[index + 1]
  }
  const HOST = String(flag('--host', '127.0.0.1'))
  const PORT = Number(flag('--port', '8787'))
  const ws = await import('ws')
  const { httpServer } = createRelayApp({ WebSocketServer: ws.WebSocketServer, WebSocket: ws.default })
  httpServer.listen(PORT, HOST, () => {
    process.stdout.write(`dsh-desktop relay listening on http://${HOST}:${String(httpServer.address().port)}\n`)
  })
}
