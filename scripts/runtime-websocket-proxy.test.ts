import { createServer, type Server } from 'node:http'
import { connect } from 'node:net'
import { afterEach, describe, expect, it } from 'vitest'
import { attachRuntimeWebSocketProxy } from './runtime-websocket-proxy.mjs'

const servers: Server[] = []

afterEach(async () => {
  await Promise.all(servers.splice(0).map((server) => closeServer(server)))
})

describe('managed Runtime WebSocket proxy', () => {
  it.each(['/api/events.mux', '/api/events.host'])('forwards %s with browser-visible trust headers', async (path) => {
    let observed: { url?: string; host?: string; origin?: string } = {}
    const backend = createServer()
    backend.on('upgrade', (request, socket) => {
      observed = {
        url: request.url,
        host: request.headers.host,
        origin: request.headers.origin,
      }
      socket.end([
        'HTTP/1.1 101 Switching Protocols',
        'Connection: Upgrade',
        'Upgrade: websocket',
        '',
        'server-event',
      ].join('\r\n'))
    })
    const backendPort = await listen(backend)

    const proxy = createServer((_request, response) => {
      response.writeHead(404).end()
    })
    attachRuntimeWebSocketProxy(proxy, { port: backendPort })
    const proxyPort = await listen(proxy)

    const response = await rawUpgrade(proxyPort, path)

    expect(response).toContain('101 Switching Protocols')
    expect(response).toContain('server-event')
    expect(observed).toEqual({
      url: path,
      host: `127.0.0.1:${proxyPort}`,
      origin: `http://127.0.0.1:${proxyPort}`,
    })
  })

  it('rejects upgrade paths outside the Runtime event allowlist', async () => {
    let backendUpgrades = 0
    const backend = createServer()
    backend.on('upgrade', (_request, socket) => {
      backendUpgrades += 1
      socket.destroy()
    })
    const backendPort = await listen(backend)

    const proxy = createServer()
    attachRuntimeWebSocketProxy(proxy, { port: backendPort })
    const proxyPort = await listen(proxy)

    const response = await rawUpgrade(proxyPort, '/api/not-an-event-stream')

    expect(response).toContain('404 Not Found')
    expect(backendUpgrades).toBe(0)
  })
})

function listen(server: Server): Promise<number> {
  servers.push(server)
  return new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      server.off('error', reject)
      const address = server.address()
      if (!address || typeof address === 'string') reject(new Error('Expected a TCP address'))
      else resolve(address.port)
    })
  })
}

function closeServer(server: Server): Promise<void> {
  return new Promise((resolve, reject) => {
    server.close((error) => error ? reject(error) : resolve())
    server.closeAllConnections()
  })
}

function rawUpgrade(port: number, path: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const socket = connect({ host: '127.0.0.1', port })
    const chunks: Buffer[] = []
    const timer = setTimeout(() => {
      socket.destroy(new Error('Timed out waiting for upgrade response'))
    }, 2_000)

    socket.once('connect', () => {
      socket.write([
        `GET ${path} HTTP/1.1`,
        `Host: 127.0.0.1:${port}`,
        `Origin: http://127.0.0.1:${port}`,
        'Connection: Upgrade',
        'Upgrade: websocket',
        'Sec-WebSocket-Version: 13',
        'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==',
        '',
        '',
      ].join('\r\n'))
    })
    socket.on('data', (chunk) => {
      chunks.push(Buffer.from(chunk))
      const response = Buffer.concat(chunks).toString()
      if (response.includes('server-event') || response.includes('404 Not Found')) socket.end()
    })
    socket.once('error', reject)
    socket.once('close', () => {
      clearTimeout(timer)
      resolve(Buffer.concat(chunks).toString())
    })
  })
}
