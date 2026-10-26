import assert from 'node:assert/strict'
import test from 'node:test'
import http, { request as namedHttpRequest } from 'node:http'
import https from 'node:https'
import http2 from 'node:http2'
import net from 'node:net'
import { once } from 'node:events'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { basename, dirname, join } from 'node:path'
import { tmpdir } from 'node:os'
import { createRequire } from 'node:module'
import { ModelProxyRuntime } from '../src/model-proxy-runtime.ts'
import { createModelProxySnapshot } from '../src/model-proxy-routing.ts'
import { opensslBin } from './test-openssl.ts'

const require = createRequire(import.meta.url)
const rawRequest = http.request
const lookup = (_hostname: string, options: any, callback: any) => {
  if (typeof options === 'function') { callback = options; options = {} }
  callback(null, options?.all ? [{ address: '127.0.0.1', family: 4 }] : '127.0.0.1', 4)
}
const directSystem = { status: 'direct' as const, httpProxy: '', httpsProxy: '', bypass: [] }
const direct = () => createModelProxySnapshot({ mode: 'direct', url: '', bypass: [] }, directSystem)
const manual = (url: string, bypass: string[] = []) => createModelProxySnapshot({ mode: 'manual', url, bypass }, directSystem)
const tick = () => new Promise<void>(resolve => setImmediate(resolve))

function gate() {
  let resolve!: () => void
  const promise = new Promise<void>(r => { resolve = r })
  return { promise, resolve }
}

async function listen(server: net.Server) {
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  return (server.address() as net.AddressInfo).port
}
function managed(server: net.Server) {
  const sockets = new Set<net.Socket>()
  server.on('connection', socket => { sockets.add(socket); socket.once('close', () => sockets.delete(socket)) })
  return async () => {
    for (const socket of sockets) socket.destroy()
    if (server.listening) await new Promise<void>(resolve => server.close(() => resolve()))
  }
}

async function fixture() {
  const parent = realpathSync(tmpdir())
  const directory = mkdtempSync(join(parent, 'dsh-model-proxy-fixture-'))
  // Cleanup is restricted to this exact newly-created owned directory.
  const cleanDirectory = () => {
    const resolved = realpathSync(directory)
    assert.equal(dirname(resolved), parent)
    assert.ok(basename(resolved).startsWith('dsh-model-proxy-fixture-'))
    assert.equal(resolved, directory)
    rmSync(resolved, { recursive: true, force: true })
  }
  const closes: (() => Promise<void>)[] = []
  try {
    const config = join(directory, 'openssl.cnf')
    const keyPath = join(directory, 'fixture-key.pem')
    const certPath = join(directory, 'fixture-cert.pem')
    writeFileSync(config, '[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=model.test\n[ext]\nsubjectAltName=DNS:model.test,DNS:other.test,IP:127.0.0.1\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n')
    execFileSync(opensslBin(), ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-config', config, '-keyout', keyPath, '-out', certPath], { stdio: 'ignore' })
    const key = readFileSync(keyPath)
    const ca = readFileSync(certPath)
    const hits: { path: string; headers: http.IncomingHttpHeaders; method?: string; body: string }[] = []
    const held = new Set<http.ServerResponse>()
    const handler = async (req: http.IncomingMessage, res: http.ServerResponse) => {
      const chunks: Buffer[] = []
      for await (const chunk of req) chunks.push(Buffer.from(chunk))
      const record = { path: req.url!, headers: req.headers, method: req.method, body: Buffer.concat(chunks).toString(), clientCertificate: (req.socket as any).getPeerCertificate?.()?.subject?.CN }
      hits.push(record)
      if (req.url === '/hold' || req.url === '/stream') {
        held.add(res); res.once('close', () => held.delete(res))
        if (req.url === '/stream') { res.writeHead(200); res.write('fixture-stream') }
        return
      }
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify(record))
    }
    const origin = http.createServer((req, res) => { void handler(req, res) })
    const secureOrigin = https.createServer({ key, cert: ca, ca, requestCert: true, rejectUnauthorized: false }, (req, res) => { void handler(req, res) })
    closes.push(managed(origin), managed(secureOrigin))
    const httpPort = await listen(origin)
    const httpsPort = await listen(secureOrigin)
    const proxyHits: { label: string; method: string; target: string; authorization?: string }[] = []
    async function makeProxy(label: string, reject = false) {
      const server = http.createServer((req, res) => {
        proxyHits.push({ label, method: req.method!, target: req.url!, authorization: req.headers['proxy-authorization'] })
        if (reject) { res.writeHead(407); res.end(); return }
        const url = new URL(req.url!)
        assert.equal(url.hostname, 'model.test')
        assert.equal(Number(url.port), httpPort)
        const { 'proxy-authorization': _authorization, 'proxy-connection': _connection, ...headers } = req.headers
        const upstream = rawRequest({ hostname: '127.0.0.1', port: httpPort, path: url.pathname + url.search, method: req.method, headers: { ...headers, 'x-fixture-proxy': label } }, response => {
          res.writeHead(response.statusCode!, response.headers); response.pipe(res)
        })
        upstream.on('error', () => { res.writeHead(502); res.end() })
        req.pipe(upstream)
      })
      server.on('connect', (req, socket, head) => {
        proxyHits.push({ label, method: 'CONNECT', target: req.url!, authorization: req.headers['proxy-authorization'] })
        if (reject) { socket.end('HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n'); return }
        const target = new URL(`http://${req.url}`)
        assert.ok(['model.test', 'other.test'].includes(target.hostname))
        assert.ok([httpPort, httpsPort].includes(Number(target.port)))
        const upstream = net.connect(Number(target.port), '127.0.0.1', () => {
          socket.write('HTTP/1.1 200 Connection Established\r\n\r\n')
          if (head.length) upstream.write(head)
          socket.pipe(upstream); upstream.pipe(socket)
        })
        upstream.on('error', () => socket.destroy())
        socket.on('error', () => upstream.destroy())
        socket.on('close', () => upstream.destroy())
      })
      closes.push(managed(server))
      const port = await listen(server)
      return { url: `http://127.0.0.1:${port}`, server }
    }
    const a = await makeProxy('A')
    const b = await makeProxy('B')
    const rejected = await makeProxy('REJECT', true)
    return {
      key, ca, hits, held, proxyHits, a, b, rejected, httpPort, httpsPort,
      http: `http://model.test:${httpPort}`, https: `https://model.test:${httpsPort}`,
      close: async () => { for (const close of closes.reverse()) await close(); cleanDirectory() },
    }
  } catch (error) {
    for (const close of closes.reverse()) await close()
    cleanDirectory()
    throw error
  }
}

function native(request: typeof http.request, args: unknown[], body?: string): Promise<any> {
  return new Promise((resolve, reject) => {
    const callback = (response: http.IncomingMessage) => {
      const chunks: Buffer[] = []
      response.on('data', chunk => chunks.push(Buffer.from(chunk)))
      response.on('error', reject)
      response.on('end', () => {
        if (response.statusCode !== 200) { reject(new Error(`fixture-status-${response.statusCode}`)); return }
        try { resolve(JSON.parse(Buffer.concat(chunks).toString())) } catch (error) { reject(error) }
      })
    }
    const req = Reflect.apply(request, undefined, [...args, callback]) as http.ClientRequest
    req.on('error', reject)
    req.end(body)
  })
}

test('first installation preserves pre-existing v1 dispatcher and exact unscoped native calls', () => {
  const module = new URL('../src/model-proxy-runtime.ts', import.meta.url).href
  const output = execFileSync(process.execPath, ['--experimental-strip-types', '--input-type=module', '-e', `
    import assert from 'node:assert/strict';
    import http from 'node:http'; import https from 'node:https';
    import { ModelProxyRuntime } from ${JSON.stringify(module)};
    const input = Object.freeze({ sentinel: true });
    const handler = Object.freeze({}); const receiver = {};
    let calls = 0;
    const original = { dispatch(options, actual) {
      assert.equal(this, original); assert.equal(options, input); assert.equal(actual, handler); calls++; return true;
    }, close() { throw new Error('previous dispatcher must not close'); } };
    globalThis[Symbol.for('undici.globalDispatcher.1')] = original;
    const sentinel = {};
    const fn = function(...args) {
      assert.equal(this, receiver); assert.equal(args[0], input); assert.equal(args[1], handler); calls++; return sentinel;
    };
    http.request = fn; http.get = fn; https.request = fn; https.get = fn;
    const runtime = new ModelProxyRuntime();
    assert.equal(globalThis[Symbol.for('undici.globalDispatcher.1')].dispatch(input, handler), true);
    for (const method of [http.request, http.get, https.request, https.get]) assert.equal(method.call(receiver, input, handler), sentinel);
    await runtime.dispose(); assert.equal(calls, 5); console.log('isolated-first-install-ok');
  `], { encoding: 'utf8' })
  assert.match(output, /isolated-first-install-ok/)
})

// A single suite owns all process-global fixture setup; subtests run serially.
test('model-only routing against real local HTTP(S) origins and proxies', { timeout: 30_000 }, async t => {
  const envBefore = { ...process.env }
  const fetchBefore = globalThis.fetch
  const websocketBefore = globalThis.WebSocket
  const f = await fixture()
  const globals = globalThis as any
  const legacyKey = Symbol.for('undici.globalDispatcher.1')
  const modernKey = Symbol.for('undici.globalDispatcher.2')
  const priorLegacy = globals[legacyKey]
  const priorModern = globals[modernKey]
  const undici = require('undici') as typeof import('undici')
  const baseline = new undici.Agent({ connect: { lookup, ca: f.ca } })
  let modernCalls = 0
  let legacyCalls = 0
  const modern = new Proxy(baseline, {
    get(target, name) {
      if (name === 'dispatch') return (options: any, handler: any) => { modernCalls++; return target.dispatch(options, handler) }
      const value = Reflect.get(target, name, target); return typeof value === 'function' ? value.bind(target) : value
    },
  })
  undici.setGlobalDispatcher(modern)
  const originalBridge = globals[legacyKey]
  globals[legacyKey] = new Proxy(originalBridge, {
    get(target, name) {
      if (name === 'dispatch') return (options: any, handler: any) => { legacyCalls++; return target.dispatch(options, handler) }
      const value = Reflect.get(target, name, target); return typeof value === 'function' ? value.bind(target) : value
    },
  })
  const options = { agentOptions: { connect: { lookup, ca: f.ca } }, proxyAgentOptions: { requestTls: { ca: f.ca } } }
  const runtimes = new Set<ModelProxyRuntime>()
  const runtime = () => { const result = new ModelProxyRuntime(options); runtimes.add(result); return result }
  try {
    const r = runtime()
    await t.test('native fetch/Undici same-domain concurrent requests isolate model and non-model', async () => {
      const baselineCalls = legacyCalls
      const proxyCount = f.proxyHits.length
      const [model, nonModel, moduleFetch] = await Promise.all([
        r.run(manual(f.a.url), async () => (await fetch(`${f.http}/scoped`)).json()),
        (await fetch(`${f.http}/tool`)).json(),
        (await undici.fetch(`${f.http}/ordinary-undici`)).json(),
      ]) as any[]
      assert.equal(model.headers['x-fixture-proxy'], 'A')
      assert.equal(nonModel.headers['x-fixture-proxy'], undefined)
      assert.equal(moduleFetch.headers['x-fixture-proxy'], undefined)
      assert.equal(legacyCalls, baselineCalls + 1)
      assert.ok(modernCalls > 0)
      assert.equal(f.proxyHits.length, proxyCount + 1)
    })

    await t.test('HTTPS fetch uses CONNECT and preserves trusted TLS validation', async () => {
      const before = f.proxyHits.length
      const response = await r.run(manual(f.a.url), async () => (await fetch(`${f.https}/tls`)).json()) as any
      assert.equal(response.path, '/tls')
      assert.equal(f.proxyHits[before]?.method, 'CONNECT')
      assert.equal(f.proxyHits[before]?.target, `model.test:${f.httpsPort}`)
    })

    await t.test('direct and loopback do not use previous environment-like dispatcher or proxy', async () => {
      const before = legacyCalls
      const proxies = f.proxyHits.length
      await r.run(direct(), async () => (await fetch(`${f.http}/direct`)).text())
      await r.run(manual(f.rejected.url), async () => (await fetch(`http://127.0.0.1:${f.httpPort}/loopback`)).text())
      assert.equal(legacyCalls, before)
      assert.equal(f.proxyHits.length, proxies)
    })

    await t.test('407 and disconnected proxy never fall back to origin', async () => {
      const before = f.hits.length
      await assert.rejects(r.run(manual(f.rejected.url), async () => (await fetch(`${f.http}/must-not-leak`)).text()))
      const closed = http.createServer()
      const port = await listen(closed)
      await new Promise<void>(resolve => closed.close(() => resolve()))
      await assert.rejects(r.run(manual(`http://127.0.0.1:${port}`), () => fetch(`${f.https}/must-not-leak`)))
      assert.equal(f.hits.length, before)
    })

    await t.test('system detection errors fail closed but an explicit user bypass works', async () => {
      const failed = createModelProxySnapshot({ mode: 'system', url: '', bypass: [] }, { ...directSystem, status: 'error', error: 'system-proxy-read-failed' })
      const before = f.hits.length
      await assert.rejects(r.run(failed, () => fetch(`${f.http}/error`)))
      assert.equal(f.hits.length, before)
      const allowed = createModelProxySnapshot({ mode: 'system', url: '', bypass: ['model.test'] }, failed.system)
      await r.run(allowed, async () => (await fetch(`${f.http}/exception`)).text())
      assert.equal(f.hits.length, before + 1)
    })

    await t.test('stream factory/next are scoped, consumer tool fetch is not; old snapshot survives switch', async () => {
      const a = manual(f.a.url)
      const stream = r.wrapStream(a, async function* () {
        yield await (await fetch(`${f.http}/old-1`)).json()
        yield await (await fetch(`${f.http}/old-2`)).json()
      })
      assert.equal((await stream.next()).value.headers['x-fixture-proxy'], 'A')
      const tools = await (await fetch(`${f.http}/consumer-tool`)).json() as any
      assert.equal(tools.headers['x-fixture-proxy'], undefined)
      const changed = await r.run(manual(f.b.url), async () => (await fetch(`${f.http}/new`)).json()) as any
      assert.equal(changed.headers['x-fixture-proxy'], 'B')
      assert.equal((await stream.next()).value.headers['x-fixture-proxy'], 'A')
      assert.equal((await stream.next()).done, true)
    })

    await t.test('return and throw cleanup executes in original scope without entering consumer scope', async () => {
      for (const action of ['return', 'throw'] as const) {
        const marker = new Error('fixture consumer error')
        let cleanup: any
        const stream = r.wrapStream(manual(f.a.url), async function* () {
          try { yield 'first'; yield 'second' }
          finally { cleanup = await (await fetch(`${f.http}/cleanup-${action}`)).json() }
        })
        await stream.next()
        await r.run(manual(f.b.url), async () => (await fetch(`${f.http}/change-${action}`)).text())
        if (action === 'return') await stream.return?.()
        else await assert.rejects(stream.throw!(marker), error => error === marker)
        assert.equal(cleanup.headers['x-fixture-proxy'], 'A')
        assert.equal(r.currentScope(), undefined)
      }
    })

    await t.test('two runtime instances and nested scopes choose their own routes', async () => {
      const other = runtime()
      const [a, b] = await Promise.all([
        r.run(manual(f.a.url), async () => (await fetch(`${f.http}/instance-a`)).json()),
        other.run(manual(f.b.url), async () => (await fetch(`${f.http}/instance-b`)).json()),
      ]) as any[]
      assert.equal(a.headers['x-fixture-proxy'], 'A')
      assert.equal(b.headers['x-fixture-proxy'], 'B')
      await r.run(manual(f.a.url), async () => {
        const nested = await other.run(manual(f.b.url), async () => (await fetch(`${f.http}/nested`)).json()) as any
        assert.equal(nested.headers['x-fixture-proxy'], 'B')
        assert.equal((await (await fetch(`${f.http}/after-nested`)).json() as any).headers['x-fixture-proxy'], 'A')
      })
      const oldStream = r.wrapStream(manual(f.a.url), async function* () {
        yield 'started'
        yield await (await fetch(`${f.http}/nested-old-stream`)).json()
      })
      await oldStream.next()
      await other.run(manual(f.b.url), async () => {
        assert.equal((await oldStream.next()).value.headers['x-fixture-proxy'], 'A')
        assert.equal((await (await fetch(`${f.http}/outer-after-old-stream`)).json() as any).headers['x-fixture-proxy'], 'B')
      })
      await oldStream.return?.()
      await other.dispose()
    })

    await t.test('native HTTP URL/string/options signatures preserve headers, method and body', async () => {
      const source = new http.Agent({ lookup, keepAlive: true })
      try {
        for (const args of [
          [`${f.http}/string`, { method: 'POST', headers: { 'x-test': 'string' }, agent: source }],
          [new URL(`${f.http}/url`), { method: 'POST', headers: { 'x-test': 'url' }, agent: source }],
          [{ hostname: 'model.test', port: f.httpPort, path: '/options', method: 'POST', headers: { 'x-test': 'options' }, agent: source }],
        ]) {
          const result = await r.run(manual(f.a.url), () => native(namedHttpRequest, args, 'fixture-body'))
          assert.equal(result.headers['x-fixture-proxy'], 'A')
          assert.ok(result.headers['x-test'])
          assert.equal(result.method, 'POST')
          assert.equal(result.body, 'fixture-body')
        }
        const nonModel = await native(http.request, [`${f.http}/native-tool`, { agent: source }])
        assert.equal(nonModel.headers['x-fixture-proxy'], undefined)
        const unproxied = await r.run(direct(), () => native(http.request, [`${f.http}/native-direct`, { agent: source }]))
        assert.equal(unproxied.headers['x-fixture-proxy'], undefined)
      } finally { source.destroy() }
    })

    await t.test('native model/non-model same-domain concurrency preserves an environment-proxied core agent outside scope', async () => {
      const source = new http.Agent({ lookup, proxyEnv: { HTTP_PROXY: f.b.url }, keepAlive: true } as any)
      try {
        const [model, tool, explicitDirect] = await Promise.all([
          r.run(manual(f.a.url), () => native(http.request, [`${f.http}/native-model`, { agent: source }])),
          native(http.request, [`${f.http}/native-env-tool`, { agent: source }]),
          r.run(direct(), () => native(http.request, [`${f.http}/native-no-env`, { agent: source }])),
        ])
        assert.equal(model.headers['x-fixture-proxy'], 'A')
        assert.equal(tool.headers['x-fixture-proxy'], 'B')
        assert.equal(explicitDirect.headers['x-fixture-proxy'], undefined)
      } finally { source.destroy() }
    })

    await t.test('native TLS/auth failures and ambiguous proxy Host fail closed', async () => {
      const before = f.hits.length
      await assert.rejects(r.run(manual(f.a.url), () => native(https.request, [`${f.https}/untrusted`])) )
      await assert.rejects(r.run(manual(f.rejected.url), () => native(http.request, [`${f.http}/rejected`])) )
      await assert.rejects(r.run(manual(f.a.url), () => native(http.request, [`${f.http}/host-override`, { headers: { Host: 'other.test' } }])), /model-proxy-native-host-unsupported/)
      assert.equal(f.hits.length, before)
    })

    await t.test('native HTTPS preserves core agent CA/SNI and request headers through CONNECT', async () => {
      const source = new https.Agent({ ca: f.ca, cert: f.ca, key: f.key, lookup, servername: 'model.test', keepAlive: true })
      try {
        const before = f.proxyHits.length
        const result = await r.run(manual(f.a.url), () => native(https.request, [new URL(`${f.https}/native-tls`), { agent: source, headers: { 'x-test': 'tls' } }]))
        assert.equal(result.headers['x-test'], 'tls')
        assert.equal(result.clientCertificate, 'model.test')
        assert.equal(f.proxyHits[before]?.method, 'CONNECT')
        await r.run(direct(), () => native(https.request, [`${f.https}/native-tls-direct`, { agent: source }]))
      } finally { source.destroy() }
    })

    await t.test('native get and agent:false are routed without mutating supplied options', async () => {
      const input = Object.freeze({ hostname: 'model.test', port: f.httpPort, path: '/get', agent: false as const, lookup, headers: Object.freeze({ 'x-test': 'get' }) })
      const result = await r.run(manual(f.a.url), () => new Promise<any>((resolve, reject) => {
        const req = http.get(input, response => {
          let body = ''; response.on('data', chunk => { body += chunk }); response.on('end', () => resolve(JSON.parse(body)))
        }); req.on('error', reject)
      }))
      assert.equal(result.headers['x-fixture-proxy'], 'A')
      assert.equal(input.agent, false)
    })

    await t.test('native custom transports fail closed, ordinary unscoped custom agents still work', async () => {
      class Custom extends http.Agent {}
      const agent = new Custom({ lookup })
      try {
        const before = f.hits.length
        await assert.rejects(r.run(manual(f.a.url), () => native(http.request, [`${f.http}/custom`, { agent }])), /model-proxy-custom-agent-unsupported/)
        await assert.rejects(r.run(direct(), () => native(http.request, [`${f.http}/custom`, { createConnection: () => net.connect(f.httpPort) }])), /model-proxy-custom-connection-unsupported/)
        assert.equal(f.hits.length, before)
        await native(http.request, [`${f.http}/custom-tool`, { agent }])
      } finally { agent.destroy() }
    })

    await t.test('model raw CONNECT/upgrade is explicitly unsupported rather than leaking sockets', async () => {
      const before = f.hits.length
      await assert.rejects(r.run(manual(f.a.url), () => native(http.request, [`${f.http}/upgrade`, { headers: { Connection: 'Upgrade', Upgrade: 'websocket' } }])), /model-proxy-upgrade-unsupported/)
      await assert.rejects(r.run(direct(), () => undici.connect(f.http)), /model-proxy-upgrade-unsupported/)
      assert.equal(f.hits.length, before)
    })

    await t.test('HTTP2 cannot open or reuse a session from a model scope', async () => {
      const server = http2.createServer()
      server.on('stream', stream => { stream.respond({ ':status': 200 }); stream.end('ok') })
      const close = managed(server)
      const port = await listen(server)
      const session = http2.connect(`http://127.0.0.1:${port}`)
      try {
        await once(session, 'connect')
        await assert.rejects(r.run(manual(f.a.url), () => http2.connect(`http://127.0.0.1:${port}`)), /model-proxy-http2-unsupported/)
        await assert.rejects(r.run(direct(), () => session.request({ ':path': '/' })), /model-proxy-http2-unsupported/)
        const req = session.request({ ':path': '/' }); req.resume(); await once(req, 'end')
      } finally { session.destroy(); await close() }
    })

    await t.test('abort releases fetch/native leases and never retries direct', async () => {
      for (const kind of ['fetch', 'native']) {
        const controller = new AbortController()
        const promise = r.run(manual(f.a.url), () => kind === 'fetch'
          ? fetch(`${f.http}/hold`, { signal: controller.signal })
          : native(http.request, [`${f.http}/hold`, { signal: controller.signal }]))
        const rejected = assert.rejects(promise)
        for (let i = 0; i < 100 && f.held.size === 0; i++) await tick()
        assert.ok(f.held.size > 0)
        controller.abort()
        await rejected
        for (let i = 0; i < 100 && f.held.size > 0; i++) await tick()
      }
    })

    await t.test('dispose waits for a live stream; next config cannot interrupt it', async () => {
      const retiring = runtime()
      const stream = retiring.wrapStream(manual(f.a.url), async function* () {
        yield 'first'
        yield await (await fetch(`${f.http}/after-dispose`)).json()
      })
      await stream.next()
      let disposed = false
      const completion = retiring.dispose().then(() => { disposed = true })
      await tick(); assert.equal(disposed, false)
      await assert.rejects(retiring.run(direct(), async () => {}), /runtime-disposed/)
      assert.equal((await stream.next()).value.headers['x-fixture-proxy'], 'A')
      await stream.return?.()
      await completion
      assert.equal(disposed, true)
      assert.equal(retiring.dispose(), retiring.dispose())
    })

    await t.test('dispose also waits for fetch response bodies after run has returned headers', async () => {
      const retiring = runtime()
      const response = await retiring.run(manual(f.a.url), () => fetch(`${f.http}/stream`))
      let disposed = false
      const completion = retiring.dispose().then(() => { disposed = true })
      await tick(); assert.equal(disposed, false)
      await response.body!.cancel()
      await completion
      assert.equal(disposed, true)
    })

    await t.test('detached async work from a released scope fails closed, not as a tool', async () => {
      const continueWork = gate()
      let detached!: Promise<unknown>
      const before = f.hits.length
      await r.run(manual(f.a.url), async () => {
        detached = continueWork.promise.then(() => fetch(`${f.http}/detached`))
      })
      const rejected = assert.rejects(detached)
      continueWork.resolve(); await rejected
      assert.equal(f.hits.length, before)
    })

    await t.test('frozen snapshots, factory failures and unstarted return do not leak scopes', async () => {
      const mutable = { config: { mode: 'manual' as const, url: f.a.url, bypass: [] }, system: directSystem }
      const frozen = r.snapshot(mutable)
      mutable.config.url = f.b.url
      assert.equal(r.snapshot(mutable), frozen)
      const result = await r.run(frozen, async () => (await fetch(`${f.http}/frozen`)).json()) as any
      assert.equal(result.headers['x-fixture-proxy'], 'A')
      const bad = r.wrapStream(frozen, () => { throw new Error('fixture-factory-failed') })
      await assert.rejects(bad.next(), /fixture-factory-failed/)
      const unstarted = r.wrapStream(frozen, () => { throw new Error('must-not-start') })
      await unstarted.return?.()
      assert.equal(r.currentScope(), undefined)
    })

    await t.test('proxy credentials stay at proxy, not origin', async () => {
      const url = new URL(f.a.url); url.username = 'fixture-user'; url.password = 'fixture-password'
      const before = f.proxyHits.length
      await r.run(manual(url.href), async () => (await fetch(`${f.https}/credentials`)).text())
      assert.equal(f.proxyHits[before]?.authorization, `Basic ${Buffer.from('fixture-user:fixture-password').toString('base64')}`)
      assert.equal(f.hits.at(-1)?.headers['proxy-authorization'], undefined)
    })

    assert.ok(JSON.stringify({ ...process.env }) === JSON.stringify(envBefore), 'runtime must not mutate environment')
    assert.equal(globalThis.fetch, fetchBefore)
    assert.equal(globalThis.WebSocket, websocketBefore)
  } finally {
    await Promise.all([...runtimes].map(runtime => runtime.dispose()))
    globals[legacyKey] = priorLegacy
    globals[modernKey] = priorModern
    await baseline.close()
    await f.close()
  }
})
