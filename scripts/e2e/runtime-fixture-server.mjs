import { createHash, generateKeyPairSync, sign } from 'node:crypto'
import { canonicalJson } from '../canonical-json.mjs'
import { startLoopbackHttps } from './tls-fixture.mjs'

const scenarios = new Set([
  'success',
  'bad-signature',
  'tampered-archive',
  'wrong-target',
  'http-redirect',
  'unknown-host',
  'disconnect-once',
  'delayed',
  'probe-exit',
])

export async function startRuntimeFixture(options = {}) {
  const archive = Buffer.from(options.archive ?? 'deepseek-harness-runtime-fixture')
  if (archive.length === 0) throw new Error('Runtime fixture archive 不能为空')

  const version = options.version ?? '9.9.9-e2e'
  const requests = []
  const signing = options.signing ?? createRuntimeSigningMaterial()
  let scenario = 'success'
  let disconnected = false
  let origin = ''

  const listener = await startLoopbackHttps(async (request, response) => {
    const url = new URL(request.url ?? '/', origin)
    requests.push(Object.freeze({
      method: request.method ?? 'GET',
      path: url.pathname,
      range: headerValue(request.headers.range),
      at: new Date().toISOString(),
    }))

    if (url.pathname === '/manifest.json') {
      sendJson(response, runtimeManifest({
        archive,
        version,
        origin,
        scenario,
        signing,
        signature: options.signature,
        healthPath: options.healthPath,
      }))
      return
    }

    if (url.pathname !== '/runtime.zip') {
      response.writeHead(404).end('not found')
      return
    }

    if (scenario === 'http-redirect') {
      response.writeHead(302, { location: `http://127.0.0.1:${listener.port}/runtime.zip` }).end()
      return
    }
    if (scenario === 'unknown-host') {
      response.writeHead(302, { location: 'https://unknown.invalid/runtime.zip' }).end()
      return
    }
    if (scenario === 'delayed') {
      await delay(options.delayMs ?? 200)
    }

    const body = scenario === 'tampered-archive' ? tamper(archive) : archive
    const range = parseRange(request.headers.range, body.length)
    const selected = body.subarray(range.start, range.end + 1)
    const headers = {
      'accept-ranges': 'bytes',
      'content-length': String(selected.length),
      'content-type': 'application/zip',
    }
    if (range.partial) headers['content-range'] = `bytes ${range.start}-${range.end}/${body.length}`
    response.writeHead(range.partial ? 206 : 200, headers)

    if (scenario === 'disconnect-once' && !disconnected) {
      disconnected = true
      response.write(selected.subarray(0, Math.max(1, Math.floor(selected.length / 2))))
      response.socket?.destroy()
      return
    }
    response.end(selected)
  }, options.tls)
  origin = listener.url

  return Object.freeze({
    version,
    url: listener.url,
    manifestUrl: `${listener.url}/manifest.json`,
    publicKey: signing.publicKey,
    caCertificate: listener.tls.caCertificate,
    setScenario(next) {
      if (!scenarios.has(next)) throw new Error(`未知 Runtime fixture 场景：${next}`)
      scenario = next
      if (next === 'disconnect-once') disconnected = false
    },
    requests: () => requests.slice(),
    clearRequests: () => { requests.length = 0 },
    close: listener.close,
  })
}

function runtimeManifest({ archive, version, origin, scenario, signing, signature, healthPath }) {
  const unsigned = {
    schemaVersion: 1,
    version,
    dshVersion: '0.1.0-rc.7',
    target: scenario === 'wrong-target' ? 'linux-x86_64' : 'windows-x86_64',
    url: `${origin}/runtime.zip`,
    size: archive.length,
    sha256: createHash('sha256').update(archive).digest('hex'),
    archive: 'zip',
    entrypoint: 'node.exe',
    args: scenario === 'probe-exit'
      ? ['app/launcher.mjs', '--e2e-probe-exit', '1']
      : ['app/launcher.mjs', '--port', '{port}'],
    healthPath: healthPath ?? '/__desktop/health',
  }
  const validSignature = signature ?? sign(null, Buffer.from(canonicalJson(unsigned)), signing.privateKey).toString('base64url')
  return { ...unsigned, signature: scenario === 'bad-signature' ? corruptSignature(validSignature) : validSignature }
}

function createRuntimeSigningMaterial() {
  const { privateKey, publicKey } = generateKeyPairSync('ed25519')
  const publicJwk = publicKey.export({ format: 'jwk' })
  if (typeof publicJwk.x !== 'string') throw new Error('无法导出 E2E Runtime Ed25519 公钥')
  return { privateKey, publicKey: publicJwk.x }
}

function corruptSignature(value) {
  if (value.length === 0) return 'invalid-signature'
  return `${value[0] === 'A' ? 'B' : 'A'}${value.slice(1)}`
}

function tamper(value) {
  const copy = Buffer.from(value)
  copy[0] ^= 0xff
  return copy
}

function parseRange(value, length) {
  if (value === undefined) return { start: 0, end: length - 1, partial: false }
  const match = /^bytes=(\d+)-(\d*)$/.exec(headerValue(value) ?? '')
  if (match === null) throw new Error(`不支持的 Range：${String(value)}`)
  const start = Number(match[1])
  const requestedEnd = match[2] === '' ? length - 1 : Number(match[2])
  if (!Number.isSafeInteger(start) || !Number.isSafeInteger(requestedEnd) || start < 0 || start >= length || requestedEnd < start) {
    throw new Error(`Range 越界：${String(value)}`)
  }
  return { start, end: Math.min(requestedEnd, length - 1), partial: true }
}

function headerValue(value) {
  if (Array.isArray(value)) return value[0]
  return value
}

function sendJson(response, value) {
  const body = Buffer.from(JSON.stringify(value))
  response.writeHead(200, {
    'content-type': 'application/json',
    'content-length': String(body.length),
  }).end(body)
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds))
}
