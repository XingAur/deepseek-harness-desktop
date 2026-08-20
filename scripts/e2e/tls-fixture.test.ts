import { X509Certificate } from 'node:crypto'
import { Socket } from 'node:net'
import { describe, expect, it } from 'vitest'
import { createFixtureTlsMaterial, startLoopbackHttps } from './tls-fixture.mjs'

describe('E2E TLS fixture', () => {
  it('releases its random loopback port after close', async () => {
    const fixture = await startLoopbackHttps((_request, response) => response.end('ok'))
    const port = fixture.port
    await fixture.close()
    await expect(connect(port)).rejects.toThrow()
  })

  it('generates an ephemeral certificate valid only for loopback hosts', () => {
    const tls = createFixtureTlsMaterial()
    const certificate = new X509Certificate(tls.caCertificate)
    expect(certificate.checkIP('127.0.0.1')).toBe('127.0.0.1')
    expect(certificate.checkHost('localhost')).toBe('localhost')
    expect(certificate.checkHost('example.com')).toBeUndefined()
  })
})

function connect(port: number) {
  return new Promise<void>((resolveConnection, reject) => {
    const socket = new Socket()
    socket.setTimeout(500)
    socket.once('connect', () => {
      socket.destroy()
      resolveConnection()
    })
    socket.once('timeout', () => {
      socket.destroy()
      reject(new Error('connection timed out'))
    })
    socket.once('error', reject)
    socket.connect(port, '127.0.0.1')
  })
}
