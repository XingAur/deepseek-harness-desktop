import assert from 'node:assert/strict'
import fsPromises, { mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { syncBuiltinESMExports } from 'node:module'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import test from 'node:test'
import { ModelProxyManager, modelProxyErrorCode } from '../src/model-proxy-manager.ts'
import type { ModelNetworkRuntime } from '../src/model-proxy-manager.ts'
import { readModelProxyConfig, writeModelProxyConfig } from '../src/model-proxy.ts'
import { resolveModelProxyRoute } from '../src/model-proxy-routing.ts'
import type { ModelProxySnapshot } from '../src/model-proxy-routing.ts'
import type { SystemProxySnapshot } from '../src/system-proxy.ts'

const SECRET = 'fixture-password-never-public'
const OLD_PROXY = `http://fixture-user:${SECRET}@old-proxy.invalid:8123`
const NEW_PROXY = 'https://fixture-next:new-fixture-password@new-proxy.invalid:8456'
const FILE = 'overlay-custom.yaml'
const PROBE = 'https://probe.invalid/reachability'
const directSystem: SystemProxySnapshot = { status: 'direct', httpProxy: '', httpsProxy: '', bypass: [] }
const proxySystem: SystemProxySnapshot = {
  status: 'ready', httpProxy: 'http://system-http.invalid:8123', httpsProxy: 'http://system-https.invalid:8456',
  bypass: ['*.inside.test'],
}

/** Tests snapshot ownership, not transport correctness (covered by runtime fixtures). */
class FakeRuntime implements ModelNetworkRuntime {
  readonly runs: ModelProxySnapshot[] = []
  readonly streams: ModelProxySnapshot[] = []
  disposed = 0
  failure: unknown
  async run<T>(snapshot: ModelProxySnapshot, operation: () => Promise<T>): Promise<T> {
    this.runs.push(snapshot)
    if (this.failure !== undefined) throw this.failure
    return operation()
  }
  wrapStream<T>(snapshot: ModelProxySnapshot, factory: () => AsyncIterable<T>): AsyncIterable<T> {
    this.streams.push(snapshot)
    return factory()
  }
  async dispose(): Promise<void> { this.disposed++ }
}

function barrier() {
  let open!: () => void
  const promise = new Promise<void>(resolve => { open = resolve })
  return { promise, open }
}
function errorCode(code: string) {
  return (error: unknown) => error instanceof Error && error.message === code
}
function assertNoSecrets(value: unknown) {
  assert.doesNotMatch(JSON.stringify(value), /fixture-password-never-public|new-fixture-password|fixture-user|fixture-next/)
}

async function withHome(run: (home: string) => Promise<void>): Promise<void> {
  const parent = resolve(tmpdir())
  const home = await mkdtemp(join(parent, 'dsh-proxy-manager-fixture-'))
  try { await run(home) } finally {
    assert.equal(dirname(resolve(home)), parent)
    assert.ok(home.startsWith(join(parent, 'dsh-proxy-manager-fixture-')))
    await rm(home, { recursive: true, force: true })
  }
}

async function withFixture(
  run: (fixture: { home: string; runtime: FakeRuntime; manager: ModelProxyManager }) => Promise<void>,
  system: () => Promise<SystemProxySnapshot> = async () => directSystem,
): Promise<void> {
  await withHome(async home => {
    await writeModelProxyConfig(home, { mode: 'manual', url: OLD_PROXY, bypass: ['explicit.test'] })
    const runtime = new FakeRuntime()
    const manager = new ModelProxyManager(home, { runtime, system, probeUrl: PROBE, probeTimeoutMs: 1000 })
    try { await run({ home, runtime, manager }) } finally { await manager.dispose() }
  })
}

test('GET projection redacts stored and system proxy credentials without modifying settings', async () => {
  await withFixture(async ({ home, runtime, manager }) => {
    const before = await readFile(join(home, FILE), 'utf8')
    const view = await manager.view()
    assert.equal(view.mode, 'manual')
    assert.equal(view.url, 'http://old-proxy.invalid:8123')
    assert.equal(view.hasCredentials, true)
    assert.deepEqual(view.bypass, ['explicit.test'])
    assert.equal(view.system.httpsProxy, 'https://new-proxy.invalid:8456')
    assertNoSecrets(view)
    assert.equal(await readFile(join(home, FILE), 'utf8'), before)
    assert.equal(runtime.runs.length, 0)
  }, async () => ({ ...proxySystem, httpsProxy: NEW_PROXY }))
})

test('unchanged facts reuse an immutable snapshot; OS changes create a separate snapshot', async () => {
  let current = { ...proxySystem, bypass: [...proxySystem.bypass] }
  await withFixture(async ({ manager }) => {
    const first = await manager.capture()
    const second = await manager.capture()
    assert.equal(first, second)
    for (const value of [first, first.config, first.config.bypass, first.system, first.system.bypass]) assert.ok(Object.isFrozen(value))
    current.httpsProxy = 'http://changed-system.invalid:8666'
    current.bypass.push('*.changed.test')
    const third = await manager.capture()
    assert.notEqual(first, third)
    assert.equal(first.system.httpsProxy, proxySystem.httpsProxy)
    assert.deepEqual(first.system.bypass, ['*.inside.test'])
    assert.equal(third.system.httpsProxy, current.httpsProxy)
    assert.equal(await manager.capture(), third)
    current = { ...proxySystem, bypass: [...proxySystem.bypass] }
    const restored = await manager.capture()
    assert.notEqual(restored, third)
    assert.equal(restored.system.httpsProxy, first.system.httpsProxy)
  }, async () => ({ ...current, bypass: [...current.bypass] }))
})

test('concurrent saves serialize credential-preserving patches and publish only completed state', async () => {
  const entered = barrier()
  const release = barrier()
  let reads = 0
  await withFixture(async ({ home, runtime, manager }) => {
    const first = manager.save({ mode: 'manual', url: NEW_PROXY, bypass: ['first.test'] })
    await entered.promise
    const second = manager.save({ mode: 'system', bypass: ['second.test'] })
    const captured = manager.capture()
    release.open()
    const [firstView, secondView, snapshot] = await Promise.all([first, second, captured])
    assert.equal(firstView.url, 'https://new-proxy.invalid:8456')
    assert.equal(secondView.hasCredentials, true)
    assertNoSecrets(firstView)
    assertNoSecrets(secondView)
    assert.deepEqual(await readModelProxyConfig(home), { mode: 'system', url: NEW_PROXY, bypass: ['second.test'] })
    assert.equal(snapshot.config.url, NEW_PROXY)
    assert.equal(snapshot.config.mode, 'system')
    assert.deepEqual(snapshot.config.bypass, ['second.test'])
    assert.equal(runtime.disposed, 0, 'saving must not uninstall transports or interrupt old calls')
  }, async () => {
    if (++reads === 1) { entered.open(); await release.promise }
    return directSystem
  })
})

test('bad save input preserves persisted configuration, cached snapshot, and queue usability', async () => {
  await withFixture(async ({ home, manager }) => {
    const snapshot = await manager.capture()
    const before = await readFile(join(home, FILE), 'utf8')
    await assert.rejects(manager.save({ mode: 'manual', url: '' }), errorCode('manual-url-required'))
    await assert.rejects(manager.save({ mode: 'invalid', url: NEW_PROXY }), errorCode('invalid-mode'))
    await assert.rejects(manager.save({ bypass: ['http://invalid.test'] }), errorCode('invalid-bypass'))
    assert.equal(await readFile(join(home, FILE), 'utf8'), before)
    assert.equal(await manager.capture(), snapshot)
    await manager.save({ mode: 'direct' })
    assert.equal((await readModelProxyConfig(home)).url, OLD_PROXY)
    assert.equal((await readModelProxyConfig(home)).mode, 'direct')
  })
})

test('atomic rename failure preserves old bytes and old snapshot without leaking its error', async t => {
  await withFixture(async ({ home, runtime, manager }) => {
    const snapshot = await manager.capture()
    const before = await readFile(join(home, FILE), 'utf8')
    const rename = t.mock.method(fsPromises, 'rename', async (_from, to) => {
      assert.equal(to, join(home, FILE))
      throw new Error(`filesystem denied ${NEW_PROXY}`)
    })
    syncBuiltinESMExports()
    try {
      await assert.rejects(manager.save({ url: NEW_PROXY }), errorCode('config-write-failed'))
    } finally {
      rename.mock.restore()
      syncBuiltinESMExports()
    }
    assert.equal(await readFile(join(home, FILE), 'utf8'), before)
    assert.deepEqual((await readdir(home)).sort(), [FILE])
    assert.equal(await manager.capture(), snapshot)
    assert.equal(runtime.disposed, 0)
    await manager.save({ url: NEW_PROXY })
    assert.equal((await readModelProxyConfig(home)).url, NEW_PROXY)
  })
})

test('old and new streams retain their own snapshots across a save', async () => {
  await withFixture(async ({ runtime, manager }) => {
    const release = barrier()
    const old = manager.stream(async function* () { yield 'old-first'; await release.promise; yield 'old-second' })[Symbol.asyncIterator]()
    assert.deepEqual(await old.next(), { done: false, value: 'old-first' })
    const oldSnapshot = runtime.streams[0]!
    const pendingOld = old.next()
    await manager.save({ url: NEW_PROXY })
    const fresh = manager.stream(async function* () { yield 'new-first' })[Symbol.asyncIterator]()
    assert.deepEqual(await fresh.next(), { done: false, value: 'new-first' })
    const newSnapshot = runtime.streams[1]!
    assert.notEqual(newSnapshot, oldSnapshot)
    assert.deepEqual(resolveModelProxyRoute(oldSnapshot, PROBE), { kind: 'proxy', proxyUrl: OLD_PROXY, reason: 'manual' })
    assert.deepEqual(resolveModelProxyRoute(newSnapshot, PROBE), { kind: 'proxy', proxyUrl: NEW_PROXY, reason: 'manual' })
    release.open()
    assert.deepEqual(await pendingOld, { done: false, value: 'old-second' })
    assert.equal(runtime.streams.length, 2, 'old iterator is not recaptured on each chunk')
    assert.equal(runtime.disposed, 0)
    await old.return?.()
    await fresh.return?.()
  })
})

test('run receives one frozen snapshot while its caller gets the operation result', async () => {
  await withFixture(async ({ runtime, manager }) => {
    assert.equal(await manager.run(async () => 'fixture-value'), 'fixture-value')
    assert.equal(runtime.runs.length, 1)
    assert.equal(runtime.runs[0], await manager.capture())
  })
})

test('dispose rejects all new entry points without invoking runtime or stream factories', async () => {
  await withFixture(async ({ runtime, manager }) => {
    await manager.dispose()
    await manager.dispose()
    assert.equal(runtime.disposed, 1)
    await assert.rejects(manager.capture(), errorCode('model-proxy-disposed'))
    await assert.rejects(manager.view(), errorCode('model-proxy-disposed'))
    await assert.rejects(manager.run(async () => 'never'), errorCode('model-proxy-disposed'))
    assert.throws(() => manager.save({ mode: 'direct' }), errorCode('model-proxy-disposed'))
    let invoked = false
    const stream = manager.stream(async function* () { invoked = true; yield 'never' })[Symbol.asyncIterator]()
    await assert.rejects(stream.next(), errorCode('model-proxy-disposed'))
    assert.equal(invoked, false)
    assert.equal(runtime.runs.length, 0)
    assert.equal(runtime.streams.length, 0)
    assert.deepEqual(await manager.test({ mode: 'direct' }), { ok: false, error: 'model-proxy-disposed', origin: 'https://probe.invalid' })
  })
})

test('dispose during asynchronous save preparation cancels before any disk commit', async () => {
  const entered = barrier()
  const release = barrier()
  await withFixture(async ({ home, runtime, manager }) => {
    const before = await readFile(join(home, FILE), 'utf8')
    const saving = manager.save({ url: NEW_PROXY })
    const rejected = assert.rejects(saving, errorCode('model-proxy-disposed'))
    await entered.promise
    const disposing = manager.dispose()
    release.open()
    await Promise.all([rejected, disposing])
    assert.equal(await readFile(join(home, FILE), 'utf8'), before)
    assert.equal(runtime.disposed, 1)
  }, async () => { entered.open(); await release.promise; return directSystem })
})

test('dispose waits for a save already inside the atomic write to settle', async t => {
  await withFixture(async ({ home, runtime, manager }) => {
    const entered = barrier()
    const release = barrier()
    const originalRename = fsPromises.rename
    const rename = t.mock.method(fsPromises, 'rename', async (from, to) => {
      assert.equal(to, join(home, FILE))
      entered.open()
      await release.promise
      return originalRename(from, to)
    })
    syncBuiltinESMExports()
    try {
      const saving = manager.save({ url: NEW_PROXY })
      await entered.promise
      const disposing = manager.dispose()
      await Promise.resolve()
      assert.equal(runtime.disposed, 0, 'runtime cleanup must wait for accepted atomic write')
      release.open()
      await Promise.all([saving, disposing])
      assert.equal((await readModelProxyConfig(home)).url, NEW_PROXY)
      assert.equal(runtime.disposed, 1)
    } finally {
      release.open()
      rename.mock.restore()
      syncBuiltinESMExports()
    }
  })
})

test('test is a credential-free HEAD connectivity probe and never persists its proposed URL', async t => {
  const requests: { url: unknown; init: RequestInit | undefined }[] = []
  let status = 204
  t.mock.method(globalThis, 'fetch', async (url, init) => {
    requests.push({ url, init })
    return new Response(status === 204 ? null : 'fixture', { status })
  })
  await withFixture(async ({ home, runtime, manager }) => {
    const before = await readFile(join(home, FILE), 'utf8')
    const snapshot = await manager.capture()
    for (const next of [204, 302, 401, 403, 407, 503]) {
      status = next
      const result = await manager.test({ mode: 'manual', url: NEW_PROXY, bypass: [] })
      assert.deepEqual(result, {
        ok: next !== 407 && next < 500,
        error: next === 407 ? 'proxy-auth' : next >= 500 ? 'request-failed' : null,
        route: 'proxy', origin: 'https://probe.invalid',
      })
      assertNoSecrets(result)
    }
    for (const { url, init } of requests) {
      assert.equal(url, PROBE)
      assert.equal(init?.method, 'HEAD')
      assert.equal(init?.redirect, 'manual')
      assert.deepEqual(init?.headers, { accept: 'text/html, application/json' })
      assert.ok(init?.signal instanceof AbortSignal)
      assert.equal(init?.body, undefined)
    }
    assert.equal(runtime.runs.length, 6)
    assert.ok(runtime.runs.every(scope => scope.config.url === NEW_PROXY))
    assert.equal(await readFile(join(home, FILE), 'utf8'), before)
    assert.equal(await manager.capture(), snapshot)
    assert.deepEqual((await readdir(home)).sort(), [FILE])
  })
})

test('failed probes preserve saved credentials and return only safe diagnostic codes', async t => {
  t.mock.method(globalThis, 'fetch', async () => {
    throw Object.assign(new Error(`proxy connection failed ${NEW_PROXY}`), { code: 'ECONNREFUSED' })
  })
  await withFixture(async ({ home, manager }) => {
    const before = await readFile(join(home, FILE), 'utf8')
    const result = await manager.test({ mode: 'manual', bypass: [] })
    assert.deepEqual(result, { ok: false, error: 'proxy-refused', route: 'proxy', origin: 'https://probe.invalid' })
    assertNoSecrets(result)
    assert.equal(await readFile(join(home, FILE), 'utf8'), before)
    assert.equal((await readModelProxyConfig(home)).url, OLD_PROXY)
  })
})

test('unsupported system settings fail before runtime/fetch, while direct probe remains explicit', async t => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => new Response(null, { status: 204 }))
  await withFixture(async ({ home, runtime, manager }) => {
    const before = await readFile(join(home, FILE), 'utf8')
    assert.deepEqual(await manager.test({ mode: 'system' }), {
      ok: false, error: 'system-proxy-pac-unsupported', origin: 'https://probe.invalid',
    })
    assert.equal(fetch.mock.callCount(), 0)
    assert.equal(runtime.runs.length, 0)
    assert.deepEqual(await manager.test({ mode: 'direct' }), {
      ok: true, error: null, route: 'direct', origin: 'https://probe.invalid',
    })
    assert.equal(fetch.mock.callCount(), 1)
    assert.equal(runtime.runs.length, 1)
    assert.equal(await readFile(join(home, FILE), 'utf8'), before)
  }, async () => ({ ...directSystem, status: 'unsupported', error: 'system-proxy-pac-unsupported' }))
})

test('invalid proposals and malformed persisted configuration cannot silently turn into direct routing', async t => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => { throw new Error('unexpected network operation') })
  await withFixture(async ({ home, runtime, manager }) => {
    const before = await readFile(join(home, FILE), 'utf8')
    for (const [input, code] of [
      [{ mode: 'manual', url: '' }, 'manual-url-required'],
      [{ bypass: ['http://not-a-domain.test'] }, 'invalid-bypass'],
    ] as const) {
      const result = await manager.test(input)
      assert.equal(result.ok, false)
      assert.equal(result.error, code)
    }
    assert.equal(await readFile(join(home, FILE), 'utf8'), before)
    await writeFile(join(home, FILE), 'broken: [\n')
    await assert.rejects(manager.capture(), errorCode('config-read-failed'))
    await assert.rejects(manager.view(), errorCode('config-read-failed'))
    await assert.rejects(manager.run(async () => 'never'), errorCode('config-read-failed'))
    assert.deepEqual(await manager.test({ mode: 'direct' }), {
      ok: false, error: 'config-read-failed', origin: 'https://probe.invalid',
    })
    assert.equal(runtime.runs.length, 0)
    assert.equal(fetch.mock.callCount(), 0)
  })
})

test('error projection handles nested causes and cycles without returning arbitrary messages', () => {
  for (const [cause, code] of [
    [new Error('invalid-bypass'), 'invalid-bypass'],
    [{ code: 'ECONNREFUSED', message: NEW_PROXY }, 'proxy-refused'],
    [{ cause: { code: 'ENOTFOUND', message: OLD_PROXY } }, 'dns'],
    [{ statusCode: 407, message: NEW_PROXY }, 'proxy-auth'],
    [{ name: 'TimeoutError', message: OLD_PROXY }, 'timeout'],
    [{ code: 'CERT_HAS_EXPIRED', message: NEW_PROXY }, 'tls'],
    [new Error(`unexpected secret ${OLD_PROXY}`), 'unknown'],
    [OLD_PROXY, 'unknown'],
  ]) {
    const result = modelProxyErrorCode(cause)
    assert.equal(result, code)
    assertNoSecrets(result)
  }
  const cycle: { cause?: unknown; message: string } = { message: NEW_PROXY }
  cycle.cause = cycle
  assert.equal(modelProxyErrorCode(cycle), 'unknown')
})
