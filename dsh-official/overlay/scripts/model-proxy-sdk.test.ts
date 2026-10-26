/** Real SDK transport reachability against local 400 fixtures, not model generation. */
import assert from 'node:assert/strict'
import test from 'node:test'
import http from 'node:http'
import https from 'node:https'
import net from 'node:net'
import { once } from 'node:events'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve, basename } from 'node:path'
import { tmpdir } from 'node:os'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { ModelProxyRuntime } from '../src/model-proxy-runtime.ts'
import { createModelProxySnapshot } from '../src/model-proxy-routing.ts'
import { opensslBin } from './test-openssl.ts'

const file = fileURLToPath(import.meta.url)
const overlay = resolve(dirname(file), '..')
const explicitDependencies = process.env.DSH_PROXY_TEST_DEPENDENCIES
const officialModules = resolve(overlay, '../deepseek-harness/node_modules')
const moduleDirectories = explicitDependencies
  ? [resolve(explicitDependencies)]
  : [officialModules, join(officialModules, '.pnpm', 'node_modules')]
const names = ['openai', '@anthropic-ai/sdk', '@google/genai', '@smithy/node-http-handler'] as const
const hostname = 'sdk-model.test'
const system = { status: 'direct' as const, httpProxy: '', httpsProxy: '', bypass: [] }

interface PackageEntry { readonly name: string; readonly version: string; readonly url: string }
function importCondition(value: unknown): string | undefined {
  if (typeof value === 'string') return value
  if (value === null || typeof value !== 'object' || Array.isArray(value)) return undefined
  for (const [condition, target] of Object.entries(value)) {
    if (['node', 'import', 'default'].includes(condition)) {
      const selected = importCondition(target)
      if (selected) return selected
    }
  }
  return undefined
}
function installed(name: string): PackageEntry | undefined {
  for (const modules of moduleDirectories) {
    const root = join(modules, name)
    const manifest = join(root, 'package.json')
    if (!existsSync(manifest)) continue
    const pkg = JSON.parse(readFileSync(manifest, 'utf8'))
    // Select actual Node import exports. Packages without exports (Smithy)
    // use their Node main, not the bundler-only extensionless dist-es tree.
    const entry = importCondition(pkg.exports?.['.'] ?? pkg.exports) ?? pkg.main ?? './index.js'
    const path = join(root, entry)
    if (!existsSync(path)) throw new Error(`SDK entry missing: ${name}`)
    return { name, version: String(pkg.version), url: pathToFileURL(path).href }
  }
  return undefined
}
const packages = new Map(names.map(name => [name, installed(name)]))

function ownedDirectory(prefix: string) {
  const parent = realpathSync(tmpdir())
  const directory = mkdtempSync(join(parent, prefix))
  return {
    directory,
    remove() {
      const target = realpathSync(directory)
      assert.equal(target, directory)
      assert.equal(dirname(target), parent)
      assert.ok(basename(target).startsWith(prefix))
      rmSync(target, { recursive: true, force: true })
    },
  }
}

if (process.env.DSH_PROXY_SDK_FIXTURE_CHILD !== '1') {
  for (const preload of [false, true]) {
    test(preload ? 'preloaded Smithy SDK transport in a credential-isolated child' : 'lazy-loaded model SDK transports in a credential-isolated child', { timeout: 45_000 }, t => {
      const required = preload ? ['@smithy/node-http-handler'] as const : names
      const missing = required.filter(name => !packages.get(name))
      if (missing.length) {
        const reason = `official development dependencies unavailable: ${missing.join(', ')}; set DSH_PROXY_TEST_DEPENDENCIES to an installed node_modules directory`
        if (explicitDependencies) assert.fail(reason)
        t.skip(reason)
        return
      }
      const home = ownedDirectory('dsh-sdk-isolated-home-')
      try {
        // In particular Anthropic unconditionally reads CUSTOM_HEADERS. Do not
        // let SDK defaults inspect the parent user's API keys, profile or home.
        const env: NodeJS.ProcessEnv = {
          PATH: process.env.PATH ?? '', HOME: home.directory, USERPROFILE: home.directory,
          TMPDIR: home.directory, TEMP: home.directory, TMP: home.directory,
          DSH_PROXY_SDK_FIXTURE_CHILD: '1', DSH_PROXY_SDK_PRELOAD: preload ? '1' : '0',
        }
        if (explicitDependencies) env.DSH_PROXY_TEST_DEPENDENCIES = resolve(explicitDependencies)
        if (process.platform === 'win32') {
          env.SystemRoot = process.env.SystemRoot
          env.WINDIR = process.env.WINDIR
        }
        const output = execFileSync(process.execPath, ['--experimental-strip-types', '--test', file], {
          env, encoding: 'utf8', timeout: 35_000, maxBuffer: 1024 * 1024,
        })
        t.diagnostic(output.trim())
      } finally { home.remove() }
    })
  }
} else {
  test('real SDK requests reach local HTTPS through CONNECT; 400 is transport-only evidence', { timeout: 30_000 }, async t => {
    const preload = process.env.DSH_PROXY_SDK_PRELOAD === '1'
    // Only this variant loads the native SDK before runtime installation.
    const preloaded = preload ? await import(packages.get('@smithy/node-http-handler')!.url) : undefined
    const fixture = await localFixture()
    const runtime = new ModelProxyRuntime({
      agentOptions: { connect: { lookup: fixture.lookup, ca: fixture.ca } },
      proxyAgentOptions: { requestTls: { ca: fixture.ca } },
    })
    const proxied = createModelProxySnapshot({ mode: 'manual', url: fixture.proxyUrl, bypass: [] }, system)
    const direct = createModelProxySnapshot({ mode: 'direct', url: '', bypass: [] }, system)
    try {
      for (const name of preload ? ['@smithy/node-http-handler'] as const : names) {
        const pkg = packages.get(name)!
        await t.test(`${name}@${pkg.version}: proxy/direct switching and concurrent non-model isolation`, async () => {
          // Mirror the production lazy-adapter order: initialize runtime first.
          const sdk = preloaded ?? await import(pkg.url)
          const client = makeClient(name, sdk, fixture)
          try {
            const proxyMarker = `${name}-model-proxy`
            const directMarker = `${name}-model-direct`
            const toolMarker = `${name}-ordinary-tool`
            await Promise.all([
              runtime.run(proxied, () => client.call(proxyMarker)),
              ordinaryTool(fixture, toolMarker),
            ])
            const model = fixture.hits.find(hit => hit.body.includes(proxyMarker))
            const tool = fixture.hits.find(hit => hit.body.includes(toolMarker))
            assert.ok(model, 'the real SDK must reach the local origin')
            assert.ok(tool, 'parallel same-host non-model request must reach the origin')
            assert.equal(model.proxied, true)
            assert.equal(tool.proxied, false)
            assert.equal(model.method, 'POST')
            assert.match(String(model.headers['content-type']), /application\/json/)
            assert.equal(model.headers['proxy-authorization'], undefined)
            client.inspect(model, proxyMarker)
            const connections = fixture.connects.length
            assert.ok(connections > 0)
            assert.ok(fixture.connects.every(target => target === `${hostname}:${fixture.port}`))
            // Reuse this exact SDK client after the policy switch. Its fetch or
            // native agent caches must not pin the preceding proxy generation.
            await runtime.run(direct, () => client.call(directMarker))
            const changed = fixture.hits.find(hit => hit.body.includes(directMarker))
            assert.ok(changed)
            assert.equal(changed.proxied, false)
            assert.equal(fixture.connects.length, connections)
            client.inspect(changed, directMarker)
          } finally { client.close() }
        })
      }
    } finally {
      await fixture.close()
      await runtime.dispose()
    }
  })
}

interface Hit {
  readonly method: string | undefined
  readonly path: string
  readonly headers: http.IncomingHttpHeaders
  readonly body: string
  readonly proxied: boolean
}

async function localFixture() {
  const owned = ownedDirectory('dsh-sdk-transport-')
  const config = join(owned.directory, 'openssl.cnf')
  const key = join(owned.directory, 'fixture-key.pem')
  const cert = join(owned.directory, 'fixture-ca.pem')
  const servers: net.Server[] = []
  const sockets = new Set<net.Socket>()
  const register = (server: net.Server) => {
    servers.push(server)
    server.on('connection', socket => { sockets.add(socket); socket.once('close', () => sockets.delete(socket)) })
  }
  const close = async () => {
    for (const socket of sockets) socket.destroy()
    for (const server of servers.reverse()) {
      if (server.listening) await new Promise<void>(resolve => server.close(() => resolve()))
    }
    owned.remove()
  }
  try {
    writeFileSync(config, `[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=${hostname}\n[ext]\nsubjectAltName=DNS:${hostname}\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n`)
    execFileSync(opensslBin(), ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-config', config, '-keyout', key, '-out', cert], { stdio: 'ignore' })
    const ca = readFileSync(cert)
    const proxyPorts = new Set<number>()
    const hits: Hit[] = []
    const origin = https.createServer({ key: readFileSync(key), cert: ca }, async (req, res) => {
      const chunks: Buffer[] = []
      for await (const chunk of req) chunks.push(Buffer.from(chunk))
      hits.push({ method: req.method, path: req.url!, headers: req.headers, body: Buffer.concat(chunks).toString(), proxied: proxyPorts.has(req.socket.remotePort!) })
      res.writeHead(400, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ error: { code: 400, status: 'INVALID_ARGUMENT', type: 'invalid_request_error', message: 'local transport fixture; no generation' } }))
    })
    register(origin)
    origin.listen(0, '127.0.0.1'); await once(origin, 'listening')
    const port = (origin.address() as net.AddressInfo).port
    const connects: string[] = []
    const proxy = http.createServer((_req, res) => { res.writeHead(405); res.end() })
    proxy.on('connect', (req, socket, head) => {
      connects.push(req.url!)
      if (req.url !== `${hostname}:${port}`) { socket.end('HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n'); return }
      // Never resolve or dial arbitrary SDK targets: only this fixture listener.
      const upstream = net.connect(port, '127.0.0.1', () => {
        proxyPorts.add(upstream.localPort!)
        socket.write('HTTP/1.1 200 Connection Established\r\n\r\n')
        if (head.length) upstream.write(head)
        socket.pipe(upstream); upstream.pipe(socket)
      })
      sockets.add(upstream)
      upstream.once('close', () => { sockets.delete(upstream); socket.destroy() })
      upstream.on('error', () => socket.destroy())
      socket.on('error', () => upstream.destroy())
      socket.on('close', () => upstream.destroy())
    })
    register(proxy)
    proxy.listen(0, '127.0.0.1'); await once(proxy, 'listening')
    // Optional SDK packages are not typed in the overlay dependency set. This
    // callback accepts Node's overloaded lookup call forms without importing SDK types.
    const lookup = (host: string, options: any, callback: any) => {
      if (typeof options === 'function') { callback = options; options = {} }
      if (host !== hostname) { callback(new Error('fixture-target-rejected')); return }
      callback(null, options?.all ? [{ address: '127.0.0.1', family: 4 }] : '127.0.0.1', 4)
    }
    return { ca, lookup, port, hits, connects, base: `https://${hostname}:${port}`, proxyUrl: `http://127.0.0.1:${(proxy.address() as net.AddressInfo).port}`, close }
  } catch (error) { await close(); throw error }
}

type Fixture = Awaited<ReturnType<typeof localFixture>>
function ordinaryTool(fixture: Fixture, marker: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const request = https.request(`${fixture.base}/ordinary-tool`, {
      method: 'POST', ca: fixture.ca, lookup: fixture.lookup, agent: false,
      headers: { 'content-type': 'application/json', 'x-fixture-kind': 'ordinary-tool' },
    }, response => {
      response.resume()
      response.on('error', reject)
      response.on('end', () => response.statusCode === 400 ? resolve() : reject(new Error('unexpected fixture status')))
    })
    request.on('error', reject)
    request.end(JSON.stringify({ marker }))
  })
}

async function rejects400(operation: PromiseLike<unknown>): Promise<void> {
  await assert.rejects(Promise.resolve(operation), (error: any) => error?.status === 400 || error?.$metadata?.httpStatusCode === 400)
}

// Dynamic SDK types intentionally remain local to this optional integration test;
// static imports would break the normal overlay check when upstream is uninstalled.
function makeClient(name: typeof names[number], sdk: any, fixture: Fixture) {
  const options = { maxRetries: 0, timeout: 4_000, logLevel: 'off' }
  if (name === 'openai') {
    const client = new sdk.default({ ...options, baseURL: `${fixture.base}/v1`, apiKey: 'fixture-openai-key', adminAPIKey: null, organization: null, project: null, webhookSecret: null })
    return {
      call: (marker: string) => rejects400(client.chat.completions.create({ model: 'fixture-model', messages: [{ role: 'user', content: marker }], max_tokens: 1 })),
      inspect(hit: Hit, marker: string) {
        assert.equal(hit.path, '/v1/chat/completions')
        assert.equal(hit.headers.authorization, 'Bearer fixture-openai-key')
        assert.equal(JSON.parse(hit.body).messages[0].content, marker)
      },
      close() {},
    }
  }
  if (name === '@anthropic-ai/sdk') {
    const client = new sdk.default({ ...options, baseURL: fixture.base, apiKey: 'fixture-anthropic-key', authToken: null })
    return {
      call: (marker: string) => rejects400(client.messages.create({ model: 'fixture-model', messages: [{ role: 'user', content: marker }], max_tokens: 1 })),
      inspect(hit: Hit, marker: string) {
        assert.equal(hit.path, '/v1/messages')
        assert.equal(hit.headers['x-api-key'], 'fixture-anthropic-key')
        assert.ok(hit.headers['anthropic-version'])
        assert.equal(JSON.parse(hit.body).messages[0].content, marker)
      },
      close() {},
    }
  }
  if (name === '@google/genai') {
    const client = new sdk.GoogleGenAI({ apiKey: 'fixture-google-key', vertexai: false, httpOptions: { baseUrl: fixture.base, apiVersion: 'v1', timeout: 4_000 } })
    return {
      call: (marker: string) => rejects400(client.models.generateContent({ model: 'fixture-model', contents: marker, config: { maxOutputTokens: 1 } })),
      inspect(hit: Hit, marker: string) {
        assert.equal(hit.path, '/v1/models/fixture-model:generateContent')
        assert.equal(hit.headers['x-goog-api-key'], 'fixture-google-key')
        assert.equal(JSON.parse(hit.body).contents[0].parts[0].text, marker)
      },
      close() {},
    }
  }
  const agent = new https.Agent({ ca: fixture.ca, lookup: fixture.lookup, keepAlive: true })
  const handler = new sdk.NodeHttpHandler({ httpsAgent: agent, requestTimeout: 4_000, connectionTimeout: 4_000 })
  return {
    async call(marker: string) {
      const { response } = await handler.handle({
        protocol: 'https:', hostname, port: fixture.port, method: 'POST', path: '/model/fixture-model/converse', query: {},
        headers: { 'content-type': 'application/json', authorization: 'Fixture dummy-auth' },
        body: JSON.stringify({ modelId: 'fixture-model', messages: [{ role: 'user', content: [{ text: marker }] }] }),
      })
      for await (const _chunk of response.body) { /* Drain the local error response inside its request scope. */ }
      assert.equal(response.statusCode, 400)
    },
    inspect(hit: Hit, marker: string) {
      assert.equal(hit.path, '/model/fixture-model/converse')
      assert.equal(hit.headers.authorization, 'Fixture dummy-auth')
      assert.equal(JSON.parse(hit.body).messages[0].content[0].text, marker)
    },
    close() { handler.destroy(); agent.destroy() },
  }
}
