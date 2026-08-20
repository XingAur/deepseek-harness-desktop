import { createServer } from 'node:https'
import selfsigned from 'selfsigned'

export function createFixtureTlsMaterial() {
  const generated = selfsigned.generate([
    { name: 'commonName', value: 'DeepSeek Harness E2E Loopback' },
  ], {
    algorithm: 'sha256',
    days: 2,
    keySize: 2048,
    extensions: [
      { name: 'basicConstraints', cA: true },
      {
        name: 'keyUsage',
        digitalSignature: true,
        keyEncipherment: true,
        keyCertSign: true,
      },
      { name: 'extKeyUsage', serverAuth: true },
      {
        name: 'subjectAltName',
        altNames: [
          { type: 7, ip: '127.0.0.1' },
          { type: 2, value: 'localhost' },
        ],
      },
    ],
  })

  return Object.freeze({
    key: generated.private,
    cert: generated.cert,
    caCertificate: generated.cert,
    fingerprint: generated.fingerprint,
  })
}

export async function startLoopbackHttps(handler, tls = createFixtureTlsMaterial()) {
  const server = createServer({ key: tls.key, cert: tls.cert }, handler)
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => {
      server.off('error', reject)
      resolve()
    })
  })

  const address = server.address()
  if (address === null || typeof address === 'string' || address.address !== '127.0.0.1') {
    await closeServer(server)
    throw new Error('E2E HTTPS fixture 必须绑定到 127.0.0.1 随机端口')
  }

  let closed = false
  return Object.freeze({
    url: `https://127.0.0.1:${address.port}`,
    port: address.port,
    tls,
    async close() {
      if (closed) return
      closed = true
      await closeServer(server)
    },
  })
}

async function closeServer(server) {
  server.closeIdleConnections?.()
  await new Promise((resolve, reject) => {
    server.close((error) => error === undefined ? resolve() : reject(error))
    server.closeAllConnections?.()
  })
}
