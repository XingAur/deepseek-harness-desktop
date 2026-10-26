/** Built-rc3 Loader + real LLM + overlay proxy/API composition, with local-only external fixtures. */
import assert from 'node:assert/strict'
import test from 'node:test'
import http from 'node:http'
import https from 'node:https'
import net from 'node:net'
import dns from 'node:dns'
import childProcess from 'node:child_process'
import { once } from 'node:events'
import { existsSync, mkdirSync, mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { basename, dirname, isAbsolute, join, relative, resolve } from 'node:path'
import { tmpdir } from 'node:os'
import { createRequire, syncBuiltinESMExports } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { opensslBin } from './test-openssl.ts'

const file = fileURLToPath(import.meta.url)
const overlay = resolve(dirname(file), '..')
const configuredDependencies = process.env.DSH_PROXY_TEST_DEPENDENCIES
/** Accept the repository root or either installed dependency-directory convention. */
function dependencyRoot(input: string): string {
  const path = resolve(input)
  if (basename(path) !== 'node_modules') return path
  const parent = dirname(path)
  if (basename(parent) === '.pnpm' && basename(dirname(parent)) === 'node_modules') {
    return dirname(dirname(parent))
  }
  return parent
}
const dependencies = dependencyRoot(configuredDependencies ?? resolve(overlay, '../.compat-stage/rc3'))
const bootFile = join(dependencies, 'packages/boot/app-boot/lib/index.js')
const llmFile = join(dependencies, 'packages/llm/llm/lib/index.js')
const isChild = process.argv.includes('--composition-child')

async function listen(server: net.Server): Promise<number> {
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  return (server.address() as net.AddressInfo).port
}
function managed(server: net.Server): () => Promise<void> {
  const sockets = new Set<net.Socket>()
  server.on('connection', socket => { sockets.add(socket); socket.once('close', () => sockets.delete(socket)) })
  return async () => {
    for (const socket of sockets) socket.destroy()
    if (server.listening) await new Promise<void>(resolve => server.close(() => resolve()))
  }
}
function owned(root: string, candidate: string): string {
  const target = resolve(candidate)
  const subpath = relative(root, target)
  assert.ok(subpath && !isAbsolute(subpath) && subpath !== '..' && !subpath.startsWith(`..${subpath.includes('\\') ? '\\' : '/'}`), 'fixture writes must remain under its freshly-created root')
  return target
}

async function composition(): Promise<void> {
  const root = realpathSync(process.env.DSH_PROXY_TEST_ROOT!)
  assert.ok(basename(root).startsWith('dsh-model-proxy-composition-'))
  assert.equal(process.env.HOME, join(root, 'home'))
  assert.equal(process.env.DSH_HOME, join(root, 'dsh'))
  const allowedEnv = new Set(['HOME', 'DSH_HOME', 'DSH_AGENTS_HOME', 'TMPDIR', 'PATH', 'LANG', 'NODE_EXTRA_CA_CERTS', 'DSH_PROXY_TEST_ROOT', 'DSH_PROXY_TEST_DEPENDENCIES', '__CF_USER_TEXT_ENCODING'])
  if (process.platform === 'win32') {
    for (const name of ['USERPROFILE', 'TEMP', 'TMP', 'SystemRoot', 'WINDIR', 'COMSPEC', 'PATHEXT']) allowedEnv.add(name)
  }
  const unexpectedEnv = Object.keys(process.env).filter(name => !allowedEnv.has(name))
  assert.deepEqual(unexpectedEnv, [], 'unexpected child environment variable names')
  const initialEnv = JSON.stringify({ ...process.env })
  const closes: (() => Promise<void>)[] = []
  const originHits: { path: string; method?: string; proxy?: string }[] = []
  const proxyHits: { proxy: string; target: string; method?: string; auth?: string }[] = []
  const rawRequest = http.request
  let ctx: any
  let state: any
  let baseline: any
  let previousLegacy: any
  let previousModern: any
  const globals = globalThis as any
  const legacyKey = Symbol.for('undici.globalDispatcher.1')
  const modernKey = Symbol.for('undici.globalDispatcher.2')
  const originalExecFile = childProcess.execFile
  const originalLookup = dns.lookup
  try {
    // Even a regressed fallback must not perform external DNS/network I/O.
    dns.lookup = ((hostname: string, options: any, callback?: Function) => {
      if (typeof options === 'function') { callback = options; options = {} }
      if (hostname !== 'model.test' && hostname !== '127.0.0.1') {
        queueMicrotask(() => callback!(Object.assign(new Error('fixture-external-dns-refused'), { code: 'ENOTFOUND' })))
      } else {
        queueMicrotask(() => callback!(null, options?.all ? [{ address: '127.0.0.1', family: 4 }] : '127.0.0.1', 4))
      }
    }) as typeof dns.lookup
    // System proxy discovery is an external nondeterministic input. Do not
    // read the host's VPN setup or invoke any other subprocess from this child.
    childProcess.execFile = ((command: string, args: string[], options: unknown, callback?: Function) => {
      if (typeof options === 'function') { callback = options }
      if (typeof callback !== 'function') throw new Error('fixture execFile requires a callback')
      if (command === '/usr/sbin/scutil') {
        setImmediate(() => callback(null, '<dictionary> {\n}\n', ''))
        return {} as childProcess.ChildProcess
      }
      if (/powershell\.exe$/i.test(command)) {
        setImmediate(() => callback(null, JSON.stringify({
          schemaVersion: 1, ok: true, autoDetect: false, autoConfigUrl: '', proxy: '', proxyBypass: '',
          autoDetectFound: false, autoDetectError: '',
        }), ''))
        return {} as childProcess.ChildProcess
      }
      throw new Error(`unexpected system command ${command}`)
    }) as typeof childProcess.execFile
    syncBuiltinESMExports()

    const origin = http.createServer((req, res) => {
      const proxy = req.headers['x-fixture-proxy'] as string | undefined
      originHits.push({ path: req.url!, method: req.method, proxy })
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ route: proxy ?? 'baseline', path: req.url }))
    })
    closes.push(managed(origin))
    const originPort = await listen(origin)
    const originUrl = `http://model.test:${originPort}`
    const secureOrigin = https.createServer({
      key: readFileSync(join(root, 'fixture-key.pem')),
      cert: readFileSync(join(root, 'fixture-cert.pem')),
    }, (req, res) => {
      originHits.push({ path: req.url!, method: req.method, proxy: 'tls-probe' })
      res.writeHead(200); res.end()
    })
    closes.push(managed(secureOrigin))
    const securePort = await listen(secureOrigin)

    async function proxy(label: string, reject = false): Promise<string> {
      const server = http.createServer((req, res) => {
        proxyHits.push({ proxy: label, target: req.url!, method: req.method, auth: req.headers['proxy-authorization'] })
        if (reject) { res.writeHead(407); res.end(); return }
        const target = new URL(req.url!)
        assert.equal(target.hostname, 'model.test', 'proxy may only forward fixture model traffic')
        assert.equal(Number(target.port), originPort)
        const upstream = rawRequest({ hostname: '127.0.0.1', port: originPort, path: target.pathname + target.search, method: req.method, headers: { 'x-fixture-proxy': label } }, response => {
          res.writeHead(response.statusCode!, response.headers); response.pipe(res)
        })
        upstream.on('error', () => { res.writeHead(502); res.end() })
        req.pipe(upstream)
      })
      server.on('connect', (req, socket, head) => {
        proxyHits.push({ proxy: label, target: req.url!, method: 'CONNECT', auth: req.headers['proxy-authorization'] })
        if (reject) { socket.end('HTTP/1.1 407 Proxy Authentication Required\r\nContent-Length: 0\r\n\r\n'); return }
        // The production HEAD probe retains its actual URL, but the fixture
        // proxy terminates its tunnel at this process's trusted local origin.
        assert.equal(req.url, 'opencode.ai:443', 'no arbitrary CONNECT destination is permitted')
        const upstream = net.connect(securePort, '127.0.0.1', () => {
          socket.write('HTTP/1.1 200 Connection Established\r\n\r\n')
          if (head.length) upstream.write(head)
          socket.pipe(upstream); upstream.pipe(socket)
        })
        upstream.on('error', () => socket.destroy())
        socket.on('error', () => upstream.destroy())
        socket.once('close', () => upstream.destroy())
      })
      closes.push(managed(server))
      return `http://127.0.0.1:${await listen(server)}`
    }
    const a = await proxy('A')
    const b = await proxy('B')
    const refused = await proxy('REFUSED', true)
    const credentialUrl = (url: string) => { const parsed = new URL(url); parsed.username = 'fixture'; parsed.password = 'fixture-password'; return parsed.href }
    const configuration = owned(root, join(process.env.DSH_HOME!, 'overlay-custom.yaml'))
    writeFileSync(configuration, JSON.stringify({ modelProxyMode: 'manual', modelProxyUrl: credentialUrl(a), modelProxyBypass: [], untouched: 'fixture-kept' }))

    const require = createRequire(import.meta.url)
    previousLegacy = globals[legacyKey]
    previousModern = globals[modernKey]
    const undici = require('undici') as typeof import('undici')
    const lookup = (hostname: string, options: any, callback: Function) => {
      assert.equal(hostname, 'model.test', 'the baseline may only resolve the local fixture hostname')
      callback(null, options.all ? [{ address: '127.0.0.1', family: 4 }] : '127.0.0.1', 4)
    }
    baseline = new undici.Agent({ connect: { lookup: lookup as any } })
    undici.setGlobalDispatcher(baseline)

    const servicesPath = owned(root, join(root, 'external-fixtures.mjs'))
    const adapterPath = owned(root, join(root, 'model-adapter.mjs'))
    const proxyPath = owned(root, join(root, 'overlay-proxy.mjs'))
    const configPath = owned(root, join(root, 'cordis.yml'))
    writeFileSync(servicesPath, `
      export const name = 'proxy-composition-external-fixtures';
      export const state = { routes: new Map(), requests: [], managers: [], adapterInputs: [] };
      export function apply(ctx) {
        ctx.provide('webServer', { register(route) {
          if (state.routes.has(route.path)) throw new Error('fixture duplicate route');
          state.routes.set(route.path, route.handler);
          return () => state.routes.delete(route.path);
        } });
        ctx.provide('connection', { requestRejection(req) {
          state.requests.push(req.url);
          return req.headers['x-fixture-auth'] === 'allowed' ? undefined : 401;
        } });
      }
    `)
    writeFileSync(adapterPath, `
      import { LlmAdapter } from ${JSON.stringify(pathToFileURL(llmFile).href)};
      import { state } from './external-fixtures.mjs';
      export const name = 'proxy-composition-adapter';
      export const inject = ['llm'];
      export function apply(ctx) {
        class FixtureAdapter extends LlmAdapter {
          async *stream(options) {
            state.adapterInputs.push(options);
            let text = '';
            yield { type: 'block-start', index: 0, blockType: 'text' };
            for (const step of [1, 2]) {
              const response = await fetch(${JSON.stringify(originUrl)} + '/' + options.model + '/' + step);
              if (!response.ok) throw new Error('fixture request failed');
              const body = await response.json();
              const delta = body.route + '|'; text += delta;
              yield { type: 'text-delta', index: 0, text: delta };
            }
            yield { type: 'block-end', index: 0, block: { type: 'text', text } };
            yield { type: 'finish', reason: { kind: 'stop' } };
          }
        }
        ctx.llm.registerAdapter(['fixture-model'], new FixtureAdapter());
      }
    `)
    writeFileSync(proxyPath, `
      import { applyOverlayProxyApi } from ${JSON.stringify(pathToFileURL(join(overlay, 'src/proxy-api.ts')).href)};
      import { state } from './external-fixtures.mjs';
      export const name = 'proxy-composition-overlay-proxy';
      export const inject = ['webServer', 'connection'];
      export function apply(ctx) { state.managers.push(applyOverlayProxyApi(ctx)); }
    `)
    writeFileSync(configPath, JSON.stringify([
      { id: 'external-fixtures', name: './external-fixtures.mjs' },
      { id: 'llm', name: pathToFileURL(llmFile).href },
      { id: 'fixture-adapter', name: './model-adapter.mjs' },
      { id: 'overlay-proxy', name: './overlay-proxy.mjs' },
    ], null, 2))

    const { boot } = await import(pathToFileURL(bootFile).href)
    ctx = await boot('proxy-composition-fixture', configPath)
    const entries = [...ctx.loader.entries()] as any[]
    for (const id of ['external-fixtures', 'llm', 'fixture-adapter', 'overlay-proxy']) {
      assert.equal(entries.find(entry => entry.options.id === id)?.fiber?.state, 2, `real Loader entry ${id} must be active`)
    }
    state = (await import(pathToFileURL(servicesPath).href)).state
    assert.equal(state.routes.size, 2)
    assert.equal(state.managers.length, 1)
    const manager = state.managers[0]
    const api = http.createServer((req, res) => {
      const handler = state.routes.get(req.url)
      if (handler) handler(req, res)
      else { res.writeHead(404); res.end() }
    })
    closes.push(managed(api))
    const apiOrigin = `http://127.0.0.1:${await listen(api)}`
    async function call(path: string, method = 'GET', body?: any, headers: Record<string, string> = {}) {
      const response = await fetch(`${apiOrigin}/api/desktop/proxy/${path}`, {
        method,
        headers: { 'x-fixture-auth': 'allowed', ...(body === undefined ? {} : { 'content-type': 'application/json' }), ...headers },
        ...(body === undefined ? {} : { body: JSON.stringify(body) }),
      })
      const text = await response.text()
      return { status: response.status, text, json: response.headers.get('content-type')?.includes('application/json') ? JSON.parse(text) : undefined }
    }
    const options = (model: string) => Object.freeze({ provider: 'fixture-model', model, messages: Object.freeze([]) })
    async function collect(stream: AsyncIterable<any>) { const chunks = []; for await (const chunk of stream) chunks.push(chunk); return chunks }
    async function nextDelta(iterator: AsyncIterator<any>) {
      while (true) {
        const item = await iterator.next()
        assert.equal(item.done, false, 'real LLM must reach the adapter text delta')
        if (item.value.type === 'text-delta') return item.value.text
        assert.notEqual(item.value.reason?.kind, 'error', JSON.stringify(item.value))
      }
    }
    const completed: string[] = []

    const unauthorized = await call('config', 'GET', undefined, { 'x-fixture-auth': 'denied' })
    assert.equal(unauthorized.status, 401)
    const deniedSave = await call('config', 'POST', { mode: 'direct' }, { 'x-fixture-auth': 'denied' })
    assert.equal(deniedSave.status, 401)
    const deniedProbe = await call('test', 'POST', { mode: 'manual', url: b, bypass: [] }, { 'x-fixture-auth': 'denied' })
    assert.equal(deniedProbe.status, 401)
    assert.equal(proxyHits.length, 0)
    assert.equal((await call('test')).status, 405)
    const rejectedOrigin = await call('config', 'POST', { mode: 'direct' }, { origin: 'https://untrusted.invalid' })
    assert.equal(rejectedOrigin.status, 403)
    const view = await call('config')
    assert.equal(view.status, 200)
    assert.equal(view.json.url, a)
    assert.equal(view.json.hasCredentials, true)
    assert.equal(view.text.includes('fixture-password'), false)
    completed.push('actual API authorization/CSRF gates and credential-free GET')

    const oldOptions = options('old-generation')
    const oldStream = ctx.llm.stream(oldOptions)[Symbol.asyncIterator]()
    const [firstDelta, tool] = await Promise.all([
      nextDelta(oldStream),
      fetch(`${originUrl}/same-domain-tool`).then(response => response.json()) as Promise<any>,
    ])
    assert.equal(firstDelta, 'A|')
    assert.equal(tool.route, 'baseline')
    assert.equal(state.adapterInputs[0], oldOptions)
    assert.equal(Object.isFrozen(state.adapterInputs[0]), true)
    assert.equal(Object.isFrozen(state.adapterInputs[0].messages), true)
    completed.push('real ctx.llm.stream waterfall calls next and preserves frozen GenerateOptions')
    completed.push('same-domain model A proxy and concurrent tool baseline isolation')

    const saved = await call('config', 'POST', { mode: 'manual', url: credentialUrl(b), bypass: [] })
    assert.equal(saved.status, 200)
    assert.equal(saved.json.url, b)
    assert.equal(saved.text.includes('fixture-password'), false)
    const newChunks = await collect(ctx.llm.stream(options('new-generation')))
    assert.equal(newChunks.filter(chunk => chunk.type === 'text-delta').map(chunk => chunk.text).join(''), 'B|B|')
    assert.equal(await nextDelta(oldStream), 'A|')
    while (!(await oldStream.next()).done) { /* Fully release the original stream. */ }
    const consumerTool = await (await fetch(`${originUrl}/consumer-tool`)).json() as any
    assert.equal(consumerTool.route, 'baseline')
    completed.push('API save affects only later model calls; old stream keeps A and consumer remains baseline')

    const unchangedUrl = await call('config', 'POST', { mode: 'manual', bypass: ['example.invalid'] })
    assert.equal(unchangedUrl.status, 200)
    assert.equal(unchangedUrl.json.hasCredentials, true)
    assert.ok(readFileSync(configuration, 'utf8').includes('fixture-password'))
    assert.ok(readFileSync(configuration, 'utf8').includes('fixture-kept'))
    const persistedBeforeTest = readFileSync(configuration, 'utf8')
    const beforeProbe = proxyHits.length
    const tested = await call('test', 'POST', { mode: 'manual', url: credentialUrl(a), bypass: [] })
    assert.equal(tested.status, 200)
    assert.equal(tested.json.ok, true, tested.text)
    assert.equal(tested.json.route, 'proxy')
    assert.equal(readFileSync(configuration, 'utf8'), persistedBeforeTest)
    assert.ok(proxyHits.slice(beforeProbe).some(hit => hit.proxy === 'A' && hit.method === 'CONNECT' && hit.target === 'opencode.ai:443'))
    assert.ok(originHits.some(hit => hit.method === 'HEAD' && hit.proxy === 'tls-probe'))
    assert.equal((await call('config')).json.url, b)
    const invalid = await call('config', 'POST', { mode: 'manual', url: 'socks5://127.0.0.1:1', bypass: [] })
    assert.equal(invalid.status, 400)
    assert.equal(readFileSync(configuration, 'utf8'), persistedBeforeTest)
    completed.push('omitted URL retains credentials; independent real HEAD probe/invalid save do not persist')

    assert.equal((await call('config', 'POST', { mode: 'manual', url: refused, bypass: [] })).status, 200)
    const originCount = originHits.length
    const failedChunks = await collect(ctx.llm.stream(options('failure-must-not-direct')))
    assert.equal(failedChunks.at(-1)?.type, 'finish')
    assert.equal(failedChunks.at(-1)?.reason.kind, 'error')
    assert.equal(originHits.length, originCount)
    assert.ok(proxyHits.some(hit => hit.proxy === 'REFUSED'))
    completed.push('real LLM converts proxy failure to error finish without a direct origin request')

    const proxyEntry = entries.find(entry => entry.options.id === 'overlay-proxy')
    await proxyEntry.fiber.dispose()
    await manager.dispose()
    assert.equal(state.routes.size, 0)
    const afterDispose = await collect(ctx.llm.stream(options('after-plugin-dispose')))
    assert.equal(afterDispose.filter(chunk => chunk.type === 'text-delta').map(chunk => chunk.text).join(''), 'baseline|baseline|')
    assert.equal((await call('config')).status, 404)
    completed.push('real Loader fiber disposal removes API routes and LLM interception')

    assert.equal(JSON.stringify({ ...process.env }), initialEnv)
    assert.ok(state.requests.length >= 10, 'all API requests must reach the injected connection auth service')
    console.log(JSON.stringify({ ok: true, composition: 'built rc3 app-boot.boot + real Loader/LLM + source overlay proxy registration', checks: completed, externalNetwork: false, settings: 'isolated temp HOME/DSH_HOME', modelRequests: originHits.filter(hit => hit.method !== 'HEAD').length }))
  } finally {
    if (ctx) await ctx.fiber.dispose()
    for (const manager of state?.managers ?? []) await manager.dispose()
    if (baseline) {
      globals[legacyKey] = previousLegacy
      globals[modernKey] = previousModern
      await baseline.close()
    }
    childProcess.execFile = originalExecFile
    dns.lookup = originalLookup
    syncBuiltinESMExports()
    for (const close of closes.reverse()) await close()
  }
}

if (isChild) {
  await composition()
} else {
  test('composition dependency paths normalize root, node_modules and pnpm linker directories', () => {
    const root = resolve(overlay, '../.compat-stage/rc3')
    for (const path of [root, join(root, 'node_modules'), join(root, 'node_modules/.pnpm/node_modules')]) {
      assert.equal(dependencyRoot(path), root)
      assert.equal(dependencyRoot(`${path}/`), root)
    }
    assert.equal(dependencyRoot('../.compat-stage/rc3/node_modules/.pnpm/node_modules'), resolve('../.compat-stage/rc3'))
  })

  test('real Loader composition routes LLM streams and serves isolated proxy settings APIs', { timeout: 35_000 }, async t => {
    const missingArtifacts = [bootFile, llmFile].filter(path => !existsSync(path))
    if (missingArtifacts.length) {
      if (configuredDependencies !== undefined) {
        assert.fail(`explicit DSH_PROXY_TEST_DEPENDENCIES resolved to ${dependencies}, but built artifacts are missing: ${missingArtifacts.join(', ')}`)
      }
      t.skip(`requires built rc3 app-boot/LLM artifacts; set DSH_PROXY_TEST_DEPENDENCIES to the built tree (currently ${dependencies})`)
      return
    }
    const parent = realpathSync(tmpdir())
    const root = mkdtempSync(join(parent, 'dsh-model-proxy-composition-'))
    try {
      for (const name of ['home', 'dsh', 'agents', 'tmp']) mkdirSync(owned(root, join(root, name)))
      const certificateConfig = owned(root, join(root, 'openssl.cnf'))
      const key = owned(root, join(root, 'fixture-key.pem'))
      const certificate = owned(root, join(root, 'fixture-cert.pem'))
      writeFileSync(certificateConfig, '[req]\ndistinguished_name=dn\nx509_extensions=ext\nprompt=no\n[dn]\nCN=opencode.ai\n[ext]\nsubjectAltName=DNS:opencode.ai\nbasicConstraints=critical,CA:TRUE\nkeyUsage=critical,digitalSignature,keyEncipherment,keyCertSign\n')
      const openssl = opensslBin()
      const opensslEnv: NodeJS.ProcessEnv = { HOME: join(root, 'home'), DSH_HOME: join(root, 'dsh'), TMPDIR: join(root, 'tmp'), LANG: 'C.UTF-8' }
      if (process.platform === 'win32') {
        opensslEnv.SystemRoot = process.env.SystemRoot
        opensslEnv.WINDIR = process.env.WINDIR
        opensslEnv.PATH = process.env.PATH
      } else {
        opensslEnv.PATH = '/usr/bin:/bin'
      }
      childProcess.execFileSync(openssl, ['req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1', '-config', certificateConfig, '-keyout', key, '-out', certificate], {
        stdio: 'ignore', cwd: root, env: opensslEnv,
      })
      const env: NodeJS.ProcessEnv = {
        HOME: join(root, 'home'), DSH_HOME: join(root, 'dsh'), DSH_AGENTS_HOME: join(root, 'agents'),
        TMPDIR: join(root, 'tmp'), PATH: opensslEnv.PATH, LANG: 'C.UTF-8', NODE_EXTRA_CA_CERTS: certificate,
        DSH_PROXY_TEST_ROOT: root, DSH_PROXY_TEST_DEPENDENCIES: dependencies,
      }
      if (process.platform === 'win32') {
        env.USERPROFILE = join(root, 'home')
        env.TEMP = join(root, 'tmp')
        env.TMP = join(root, 'tmp')
        env.SystemRoot = process.env.SystemRoot
        env.WINDIR = process.env.WINDIR
        env.COMSPEC = process.env.COMSPEC
        env.PATHEXT = process.env.PATHEXT
      }
      const result = await new Promise<{ stdout: string; stderr: string }>((resolve, reject) => {
        childProcess.execFile(process.execPath, ['--experimental-strip-types', file, '--composition-child'], {
          cwd: root, env, timeout: 30_000, maxBuffer: 2 * 1024 * 1024,
        }, (error, stdout, stderr) => {
          if (error) reject(new Error(`isolated composition failed: ${error.message}\n${stdout}\n${stderr}`))
          else resolve({ stdout, stderr })
        })
      })
      const reportLine = result.stdout.trim().split('\n').find(line => line.startsWith('{"ok":true'))
      assert.ok(reportLine, result.stdout + result.stderr)
      const report = JSON.parse(reportLine)
      assert.equal(report.ok, true)
      assert.equal(report.checks.length, 7)
      assert.equal(report.externalNetwork, false)
      assert.ok(report.modelRequests >= 8)
      t.diagnostic(reportLine)
    } finally {
      const target = realpathSync(root)
      assert.equal(target, root)
      assert.equal(dirname(target), parent)
      assert.ok(basename(target).startsWith('dsh-model-proxy-composition-'))
      rmSync(target, { recursive: true, force: true })
    }
  })
}
