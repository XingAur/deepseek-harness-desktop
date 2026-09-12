import { createServer, type Server } from 'node:net'
import { afterEach, describe, expect, it } from 'vitest'
import { detectLocalOutboundProxy, probeLoopbackHttpProxy } from '../src/desktop-proxy-detect.ts'

const servers: Server[] = []

function listenProxy(statusLine: string): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer(socket => {
      socket.once('data', () => {
        socket.end(`${statusLine}\r\n\r\n`)
      })
    })
    servers.push(server)
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (address === null || typeof address === 'string') {
        reject(new Error('expected a TCP address'))
        return
      }
      resolve(address.port)
    })
    server.once('error', reject)
  })
}

afterEach(() => {
  for (const server of servers.splice(0)) server.close()
})

describe('desktop local proxy detect', () => {
  it('accepts an HTTP CONNECT 200 proxy', async () => {
    const port = await listenProxy('HTTP/1.1 200 Connection Established')
    await expect(probeLoopbackHttpProxy(port)).resolves.toBe(true)
    await expect(detectLocalOutboundProxy([port])).resolves.toBe(`http://127.0.0.1:${String(port)}`)
  })

  it('accepts an authenticating HTTP proxy', async () => {
    const port = await listenProxy('HTTP/1.1 407 Proxy Authentication Required')
    await expect(probeLoopbackHttpProxy(port)).resolves.toBe(true)
  })

  it('rejects an ordinary HTTP server', async () => {
    const port = await listenProxy('HTTP/1.1 404 Not Found')
    await expect(probeLoopbackHttpProxy(port)).resolves.toBe(false)
  })

  it('returns null when no candidate answers', async () => {
    await expect(detectLocalOutboundProxy([1])).resolves.toBeNull()
  })
})
