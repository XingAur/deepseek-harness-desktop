import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test, { type TestContext } from 'node:test'
import { createContext, runInContext } from 'node:vm'
import { transformSync } from 'esbuild'

/**
 * In-memory component regression checks, NOT React DOM or browser tests.
 * The actual TSX is transformed without adding test exports or writing artifacts.
 * Only hooks, JSX, fetch and request timers are simulated. No network, real proxy,
 * application configuration, host filesystem writes or dependency changes occur.
 * Rendering, native event behavior, accessibility and CSS still need browser QA.
 *
 * Run: node --experimental-strip-types --test scripts/model-proxy-ui.test.ts
 */
const source = readFileSync(new URL('../src/client/ProxyFooter.tsx', import.meta.url), 'utf8')
const compiled = transformSync(source, {
  sourcefile: 'ProxyFooter.tsx', loader: 'tsx', format: 'cjs', jsx: 'automatic', target: 'es2022',
}).code

type Mode = 'system' | 'manual' | 'direct'
type Language = 'keys' | 'en' | 'zh'
interface ConfigFixture {
  ok: true
  mode: Mode
  url: string
  hasCredentials: boolean
  bypass: string[]
  system: {
    status: 'ready' | 'direct' | 'unsupported' | 'error'
    httpProxy: string
    httpsProxy: string
    bypass: string[]
    error?: string
  }
  error?: string
}
function config(): ConfigFixture {
  return {
    ok: true, mode: 'manual', url: 'http://proxy.example.test:7890', hasCredentials: true,
    bypass: ['local.example.test'],
    system: {
      status: 'ready', httpProxy: 'http://system-proxy.example.test:7891', httpsProxy: '',
      bypass: ['*.internal.test'],
    },
  }
}
interface VNode {
  type: unknown
  props: {
    children?: unknown
    id?: string
    type?: string
    value?: unknown
    disabled?: boolean
    checked?: boolean
    onClick?: () => void
    onChange?: (event: { target: { value: string } }) => void
    [name: string]: unknown
  }
}
interface MockResponse {
  status: number
  ok: boolean
  json: () => Promise<unknown>
}
interface MockRequest {
  url: string
  options: {
    method?: string
    credentials?: string
    redirect?: string
    cache?: string
    headers?: Record<string, string>
    body?: string
    signal: AbortSignal
  }
  resolve: (value: MockResponse) => void
  reject: (reason: unknown) => void
}
type Cleanup = () => void
type HookSlot =
  | { kind: 'state'; value: unknown }
  | { kind: 'ref'; value: { current: unknown } }
  | { kind: 'id'; value: string }
  | { kind: 'callback'; deps?: readonly unknown[]; value: unknown }
  | { kind: 'effect'; deps?: readonly unknown[]; effect: () => void | Cleanup; cleanup?: void | Cleanup }

function isNode(value: unknown): value is VNode {
  return typeof value === 'object' && value !== null && 'type' in value && 'props' in value
}
function textOf(value: unknown): string {
  if (Array.isArray(value)) return value.map(textOf).join(' ')
  if (isNode(value)) return textOf(value.props.children)
  return value == null || typeof value === 'boolean' ? '' : String(value)
}
function descendants(value: unknown, predicate: (node: VNode) => boolean, found: VNode[] = []): VNode[] {
  if (Array.isArray(value)) value.forEach(child => descendants(child, predicate, found))
  else if (isNode(value)) {
    if (predicate(value)) found.push(value)
    descendants(value.props.children, predicate, found)
  }
  return found
}
function sameDeps(left?: readonly unknown[], right?: readonly unknown[]): boolean {
  return left !== undefined && right !== undefined && left.length === right.length
    && left.every((value, index) => Object.is(value, right[index]))
}

function harness(t: TestContext, initialLanguage: Language = 'keys', respectAbort = false) {
  const slots: HookSlot[] = []
  const scheduledEffects: number[] = []
  const timers = new Map<number, () => void>()
  const requests: MockRequest[] = []
  let cursor = 0
  let timerId = 0
  let disposed = false
  let updatesAfterUnmount = 0
  let language = initialLanguage
  let locales: Record<'en' | 'zh', Record<string, string>> | undefined
  const registrations: { namespace?: string; slot?: string } = {}
  const react = {
    useState(initial: unknown) {
      const index = cursor++
      slots[index] ??= { kind: 'state', value: typeof initial === 'function' ? initial() : initial }
      const slot = slots[index]!
      assert.equal(slot.kind, 'state', 'hook order must remain stable')
      if (slot.kind !== 'state') throw new Error('unexpected hook')
      return [slot.value, (value: unknown) => {
        if (disposed) updatesAfterUnmount++
        slot.value = typeof value === 'function' ? value(slot.value) : value
      }]
    },
    useRef(initial: unknown) {
      const index = cursor++
      slots[index] ??= { kind: 'ref', value: { current: initial } }
      const slot = slots[index]!
      assert.equal(slot.kind, 'ref')
      if (slot.kind !== 'ref') throw new Error('unexpected hook')
      return slot.value
    },
    useId() {
      const index = cursor++
      slots[index] ??= { kind: 'id', value: `proxy-fixture-${index}` }
      const slot = slots[index]!
      assert.equal(slot.kind, 'id')
      if (slot.kind !== 'id') throw new Error('unexpected hook')
      return slot.value
    },
    useCallback(value: unknown, deps?: readonly unknown[]) {
      const index = cursor++
      const before = slots[index]
      assert.ok(before === undefined || before.kind === 'callback')
      if (before === undefined || !sameDeps(before.deps, deps)) slots[index] = { kind: 'callback', deps, value }
      const slot = slots[index]!
      if (slot.kind !== 'callback') throw new Error('unexpected hook')
      return slot.value
    },
    useEffect(effect: () => void | Cleanup, deps?: readonly unknown[]) {
      const index = cursor++
      const before = slots[index]
      assert.ok(before === undefined || before.kind === 'effect')
      if (before === undefined || !sameDeps(before.deps, deps)) {
        if (before?.cleanup) before.cleanup()
        slots[index] = { kind: 'effect', deps, effect }
        scheduledEffects.push(index)
      }
    },
  }
  const jsx = (type: unknown, props: VNode['props']): VNode => ({ type, props })
  const exports = {} as {
    OverlayProxyFooter: (props: { t: (key: string) => string }) => unknown
    applyOverlayProxyFooter: (ctx: unknown) => void
  }
  const module = { exports }
  const context = createContext({
    module, exports, URL, AbortController,
    require(name: string) {
      if (name === 'react') return react
      if (name === 'react/jsx-runtime') return { jsx, jsxs: jsx, Fragment: Symbol.for('test.fragment') }
      throw new Error(`Unexpected runtime dependency: ${name}`)
    },
    setTimeout(callback: () => void, delay: number) {
      assert.ok(delay > 0, 'request timeout must have a positive duration')
      const id = ++timerId
      timers.set(id, callback)
      return id
    },
    clearTimeout(id?: number) { if (id !== undefined) timers.delete(id) },
    fetch(url: string, options: MockRequest['options']) {
      return new Promise<MockResponse>((resolve, reject) => {
        const onAbort = (): void => reject(new Error('mock fetch aborted'))
        const removeListener = (): void => options.signal.removeEventListener('abort', onAbort)
        // Ignoring abort by default deliberately exercises stale-response guards.
        if (respectAbort) options.signal.addEventListener('abort', onAbort, { once: true })
        requests.push({
          url, options,
          resolve(value) { removeListener(); resolve(value) },
          reject(reason) { removeListener(); reject(reason) },
        })
      })
    },
  })
  runInContext(compiled, context, { filename: 'ProxyFooter.fixture.cjs', timeout: 1_000 })
  module.exports.applyOverlayProxyFooter({
    effect(effect: () => unknown) { return effect() },
    locale: {
      register(namespace: string, values: Record<'en' | 'zh', Record<string, string>>) {
        registrations.namespace = namespace
        locales = values
        return () => {}
      },
      bind() { return translate },
    },
    slots: {
      inject(_name: string, effect: () => unknown) { return effect() },
      register(options: { name: string }) { registrations.slot = options.name; return () => {} },
    },
  })
  assert.ok(locales)
  function translate(key: string): string {
    assert.ok(Object.hasOwn(locales!.en, key) && Object.hasOwn(locales!.zh, key), `missing locale key: ${key}`)
    return language === 'keys' ? key : locales![language][key]!
  }
  function render(): unknown {
    cursor = 0
    const tree = module.exports.OverlayProxyFooter({ t: translate })
    for (let index; (index = scheduledEffects.shift()) !== undefined;) {
      const slot = slots[index]!
      if (slot.kind !== 'effect') throw new Error('unexpected hook')
      slot.cleanup = slot.effect()
    }
    return tree
  }
  function one(predicate: (node: VNode) => boolean): VNode {
    const found = descendants(render(), predicate)
    assert.equal(found.length, 1, `expected one matching node, found ${found.length}`)
    return found[0]!
  }
  function button(key: string): VNode {
    return one(node => node.type === 'button' && textOf(node) === translate(key))
  }
  function input(suffix: string): VNode {
    return one(node => node.type === 'input' && node.props.id?.endsWith(suffix) === true)
  }
  function textarea(): VNode { return one(node => node.type === 'textarea') }
  function mode(value: Mode): VNode {
    return one(node => node.type === 'input' && node.props.type === 'radio' && node.props.value === value)
  }
  function unmount(): void {
    if (disposed) return
    for (const slot of slots) if (slot.kind === 'effect' && slot.cleanup) slot.cleanup()
    disposed = true
  }
  const api = {
    requests, registrations, locales, render, translate, one, button, input, textarea, mode,
    text: () => textOf(render()),
    language(value: Language) { language = value },
    click(key: string) {
      const node = button(key)
      assert.notEqual(node.props.disabled, true, `cannot click disabled ${key}`)
      assert.ok(node.props.onClick)
      node.props.onClick()
    },
    editUrl(value: string) {
      const node = input('-url')
      assert.notEqual(node.props.disabled, true)
      assert.ok(node.props.onChange)
      node.props.onChange({ target: { value } })
    },
    editBypass(value: string) {
      const node = textarea()
      assert.notEqual(node.props.disabled, true)
      assert.ok(node.props.onChange)
      node.props.onChange({ target: { value } })
    },
    selectMode(value: Mode) {
      assert.notEqual(one(node => node.type === 'fieldset').props.disabled, true)
      const node = mode(value)
      assert.ok(node.props.onChange)
      node.props.onChange({ target: { value } })
    },
    respond(index: number, payload: unknown, status = 200) {
      assert.ok(requests[index], `missing request ${index}`)
      requests[index]!.resolve({ status, ok: status >= 200 && status < 300, json: async () => payload })
    },
    body(index: number): Record<string, unknown> {
      assert.ok(requests[index]?.options.body)
      return JSON.parse(requests[index]!.options.body!) as Record<string, unknown>
    },
    flush: () => new Promise<void>(resolve => setImmediate(resolve)),
    fireTimeout() {
      assert.equal(timers.size, 1)
      const [id, callback] = [...timers.entries()][0]!
      timers.delete(id)
      callback()
    },
    unmount,
    strictReplay() {
      // Simulates effect setup/cleanup/setup, not React's complete StrictMode renderer.
      for (const slot of slots) if (slot.kind === 'effect') {
        if (slot.cleanup) slot.cleanup()
        slot.cleanup = slot.effect()
      }
    },
    get timerCount() { return timers.size },
    get updatesAfterUnmount() { return updatesAfterUnmount },
  }
  t.after(() => {
    unmount()
    assert.equal(timers.size, 0, 'cleanup must release every fake request timer')
    assert.equal(updatesAfterUnmount, 0, 'unmounted components must not receive state updates')
  })
  render()
  return api
}
type Harness = ReturnType<typeof harness>
async function loaded(t: TestContext, fixture = config(), language: Language = 'keys'): Promise<Harness> {
  const ui = harness(t, language)
  ui.respond(0, fixture)
  await ui.flush()
  return ui
}

const expectedErrors: Record<string, string> = {
  'invalid-config': 'errorConfig', 'invalid-mode': 'errorMode', 'invalid-url': 'errorUrl',
  'url-too-long': 'errorUrlLength', 'unsupported-proxy-scheme': 'errorScheme',
  'invalid-bypass': 'errorBypass', 'manual-url-required': 'errorNoProxy',
  'config-read-failed': 'errorRead', 'config-write-failed': 'errorWrite',
  'system-proxy-unsupported-platform': 'errorPlatform', 'system-proxy-read-failed': 'errorSystemRead',
  'system-proxy-invalid-config': 'errorSystemConfig', 'system-proxy-pac-unsupported': 'errorPac',
  'system-proxy-autodiscovery-unsupported': 'errorAutoDiscovery', 'system-proxy-socks-unsupported': 'errorSocks',
  'system-proxy-scoped-unsupported': 'errorScoped', 'system-proxy-list-unsupported': 'errorList',
  'system-proxy-bypass-unsupported': 'errorBypassUnsupported', 'no-proxy': 'errorNoProxy',
  'proxy-refused': 'errorProxyRefused', 'proxy-auth': 'errorProxyAuth', dns: 'errorDns', timeout: 'errorTimeout',
  tls: 'errorTls', unknown: 'errorUnknown', 'request-failed': 'errorUnknown', 'invalid-target-url': 'errorTarget',
  'model-proxy-disposed': 'errorRuntime', 'model-proxy-runtime-unavailable': 'errorRuntime',
  'model-proxy-transport-unsupported': 'errorTransport',
}

test('memory UI: registers matching nonempty English/Chinese locale tables at the official footer slot', t => {
  const ui = harness(t)
  assert.deepEqual(ui.registrations, { namespace: 'overlay.proxy', slot: 'settings.models.footer' })
  assert.deepEqual(Object.keys(ui.locales.en).sort(), Object.keys(ui.locales.zh).sort())
  for (const key of Object.keys(ui.locales.en)) {
    assert.ok(ui.locales.en[key]!.trim(), `empty English text: ${key}`)
    assert.ok(ui.locales.zh[key]!.trim(), `empty Chinese text: ${key}`)
  }
})

test('memory UI: all agreed host error codes localize in both languages without raw exception text', async t => {
  for (const language of ['en', 'zh'] as const) {
    const ui = await loaded(t, config(), language)
    for (const [code, key] of Object.entries(expectedErrors)) {
      ui.click('test')
      ui.respond(ui.requests.length - 1, { ok: false, error: code }, 400)
      await ui.flush()
      assert.ok(ui.text().includes(ui.locales[language][key]!), `${language}: ${code}`)
    }
    for (const raw of ['http://fixture-user:fixture-password@proxy.example.test', '__proto__', 'constructor']) {
      ui.click('test')
      ui.respond(ui.requests.length - 1, { ok: false, error: raw })
      await ui.flush()
      assert.ok(ui.text().includes(ui.locales[language].errorUnknown!))
      assert.equal(ui.text().includes(raw), false)
    }
  }
})

test('memory UI: initial load uses one safe GET and disables save/test until a valid view arrives', async t => {
  const ui = harness(t)
  assert.equal(ui.requests.length, 1)
  assert.equal(ui.requests[0]!.url, '/api/desktop/proxy/config')
  assert.equal(ui.requests[0]!.options.method, 'GET')
  assert.equal(ui.requests[0]!.options.credentials, 'same-origin')
  assert.equal(ui.requests[0]!.options.redirect, 'error')
  assert.equal(ui.requests[0]!.options.cache, 'no-store')
  assert.equal(ui.requests[0]!.options.headers?.accept, 'application/json')
  assert.equal(ui.button('save').props.disabled, true)
  assert.equal(ui.button('test').props.disabled, true)
  assert.equal(ui.mode('system').props.checked, true)
  assert.match(ui.text(), /loading/)
  ui.respond(0, config())
  await ui.flush()
  assert.equal(ui.button('save').props.disabled, false)
  assert.equal(ui.mode('manual').props.checked, true)
  assert.equal(ui.timerCount, 0)
})

test('memory UI: three draft modes do not misrepresent the saved effective mode', async t => {
  const ui = await loaded(t)
  assert.equal(descendants(ui.render(), node => node.type === 'input' && node.props.type === 'radio').length, 3)
  assert.match(ui.text(), /credentialsHint/)
  assert.match(ui.text(), /systemReady/)
  assert.match(ui.text(), /directRoute/)
  for (const mode of ['system', 'direct', 'manual'] as const) {
    ui.selectMode(mode)
    assert.equal(ui.mode(mode).props.checked, true)
    assert.match(ui.text(), /unsaved/)
    const savedMode = descendants(ui.render(), node => node.type === 'dd')[0]!
    assert.equal(textOf(savedMode), 'modeManual')
  }
})

test('memory UI: unchanged redacted URL is omitted from save in every mode and bypass entries are split', async t => {
  for (const mode of ['system', 'manual', 'direct'] as const) {
    const ui = await loaded(t)
    ui.selectMode(mode)
    ui.editBypass(' one.example.test, two.example.test\n\n 127.0.0.1 ')
    ui.click('save')
    assert.equal(ui.requests[1]!.url, '/api/desktop/proxy/config')
    assert.equal(ui.requests[1]!.options.method, 'POST')
    assert.deepEqual(ui.body(1), { mode, bypass: ['one.example.test', 'two.example.test', '127.0.0.1'] })
    assert.equal(Object.hasOwn(ui.body(1), 'url'), false)
  }
})

test('memory UI: only edited full URLs are submitted; successful save returns to credential-free display', async t => {
  const ui = await loaded(t)
  const full = 'http://fixture-user:fixture-password@new-proxy.example.test:8888'
  ui.editUrl(full)
  assert.equal(ui.input('-url').props.type, 'password')
  ui.click('test')
  assert.equal(ui.body(1).url, full)
  ui.respond(1, { ok: true, route: 'proxy' })
  await ui.flush()
  assert.equal(ui.requests.length, 2, 'testing must not issue a config POST')
  ui.click('save')
  assert.equal(ui.body(2).url, full)
  ui.respond(2, { ...config(), url: full })
  await ui.flush()
  assert.equal(ui.input('-url').props.value, 'http://new-proxy.example.test:8888')
  assert.equal(ui.input('-url').props.type, 'url')
  assert.match(ui.text(), /saved/)
  assert.equal(JSON.stringify(ui.render()).includes('fixture-password'), false)
  ui.click('save')
  assert.equal(Object.hasOwn(ui.body(3), 'url'), false, 'saved redacted URL is no longer an edit')
})

test('memory UI: invalid manual URLs fail locally, release busy state and never issue a request', async t => {
  for (const [value, key] of [['', 'errorNoProxy'], ['not a URL', 'errorUrl'], ['socks5://proxy.example.test:1080', 'errorScheme']]) {
    const ui = await loaded(t)
    ui.editUrl(value!)
    ui.click('test')
    await ui.flush()
    assert.equal(ui.requests.length, 1)
    assert.match(ui.text(), new RegExp(`testFailed.*${key}`))
    assert.equal(ui.button('test').props.disabled, false)
    assert.equal(ui.timerCount, 0)
  }
})

test('memory UI: proxy/direct/unspecified test success never saves and omits unchanged URL', async t => {
  for (const [route, key] of [['proxy', 'testOkProxy'], ['direct', 'testOkDirect'], [undefined, 'testOk']] as const) {
    const ui = await loaded(t)
    ui.click('test')
    assert.equal(ui.requests[1]!.url, '/api/desktop/proxy/test')
    assert.deepEqual(ui.body(1), { mode: 'manual', bypass: ['local.example.test'] })
    ui.respond(1, { ok: true, ...(route ? { route } : {}) })
    await ui.flush()
    assert.equal(ui.requests.length, 2)
    assert.ok(ui.text().includes(key))
    assert.equal(descendants(ui.render(), node => node.props.role === 'status').some(node => textOf(node) === 'saved'), false)
  }
})

test('memory UI: stored, system and test URLs remove userinfo, path, query and fragment before display', async t => {
  const fixture = config()
  fixture.url = 'http://fixture-user:fixture-password@proxy.example.test:7890/private?secret=query#hidden'
  fixture.system.httpProxy = 'https://system-user:system-password@system-proxy.example.test:7891/private?token=hidden'
  const ui = await loaded(t, fixture)
  assert.equal(ui.input('-url').props.value, 'http://proxy.example.test:7890')
  assert.ok(ui.text().includes('https://system-proxy.example.test:7891'))
  ui.click('test')
  ui.respond(1, { ok: true, route: 'proxy', origin: 'https://test-user:test-password@target.example.test/private?secret=query#hidden' })
  await ui.flush()
  assert.ok(ui.text().includes('https://target.example.test'))
  for (const secret of ['fixture-user', 'fixture-password', 'system-user', 'system-password', 'test-user', 'test-password', '/private', 'secret=query', '#hidden']) {
    assert.equal(JSON.stringify(ui.render()).includes(secret), false, secret)
  }
})

test('memory UI: editing URL, mode or bypass clears old test success and saved notices', async t => {
  for (const field of ['url', 'mode', 'bypass']) {
    const ui = await loaded(t)
    ui.click('test')
    ui.respond(1, { ok: true, route: 'proxy' })
    await ui.flush()
    assert.match(ui.text(), /testOkProxy/)
    if (field === 'url') ui.editUrl('http://new-proxy.example.test:8888')
    if (field === 'mode') ui.selectMode('direct')
    if (field === 'bypass') ui.editBypass('new.example.test')
    assert.doesNotMatch(ui.text(), /testOkProxy/)
    ui.click('save')
    ui.respond(2, config())
    await ui.flush()
    assert.equal(textOf(ui.one(node => node.props.role === 'status')), 'saved')
    ui.editBypass('after-save.example.test')
    assert.equal(textOf(ui.one(node => node.props.role === 'status')), 'unsaved')
  }
})

test('memory UI: synchronous duplicate actions cannot overlap and save locks inputs/buttons', async t => {
  const ui = await loaded(t)
  const save = ui.button('save').props.onClick!
  const testConnection = ui.button('test').props.onClick!
  const refresh = ui.button('refresh').props.onClick!
  save(); save(); testConnection(); refresh()
  assert.equal(ui.requests.length, 2)
  assert.equal(ui.button('saving').props.disabled, true)
  assert.equal(ui.button('test').props.disabled, true)
  assert.equal(ui.button('refresh').props.disabled, true)
  assert.equal(ui.input('-url').props.disabled, true)
  assert.equal(ui.textarea().props.disabled, true)
  assert.equal(ui.one(node => node.type === 'fieldset').props.disabled, true)
  assert.equal(ui.one(node => node.type === 'section').props['aria-busy'], true)
  ui.respond(1, config())
  await ui.flush()
  assert.equal(ui.button('save').props.disabled, false)
  assert.equal(ui.one(node => node.type === 'section').props['aria-busy'], false)
})

test('memory UI: editing aborts a test; its late completion cannot overwrite or unlock a newer save', async t => {
  for (const outcome of ['resolve', 'reject']) {
    const ui = await loaded(t)
    ui.click('test')
    ui.selectMode('direct')
    assert.equal(ui.requests[1]!.options.signal.aborted, true)
    ui.click('save')
    if (outcome === 'resolve') ui.respond(1, { ok: true, route: 'proxy' })
    else ui.requests[1]!.reject(new Error('late failure containing fixture-password'))
    await ui.flush()
    assert.equal(ui.button('saving').props.disabled, true)
    assert.doesNotMatch(ui.text(), /testOkProxy|testFailed|fixture-password/)
    assert.equal(ui.body(2).mode, 'direct')
    ui.respond(2, { ...config(), mode: 'direct' })
    await ui.flush()
    assert.match(ui.text(), /saved/)
    assert.equal(ui.mode('direct').props.checked, true)
  }
})

test('memory UI: refresh keeps edits made before and during GET while replacing effective/system status', async t => {
  const ui = await loaded(t)
  const full = 'http://new-user:new-password@new-proxy.example.test:8888'
  ui.editUrl(full)
  ui.click('refresh')
  ui.editBypass('during-refresh.example.test')
  const refreshed = config()
  refreshed.mode = 'direct'
  refreshed.system.status = 'unsupported'
  refreshed.system.error = 'system-proxy-pac-unsupported'
  ui.respond(1, refreshed)
  await ui.flush()
  assert.equal(ui.requests[1]!.options.method, 'GET')
  assert.equal(ui.requests[1]!.options.body, undefined)
  assert.equal(ui.mode('manual').props.checked, true)
  assert.equal(ui.input('-url').props.value, full)
  assert.equal(ui.input('-url').props.type, 'password')
  assert.equal(ui.textarea().props.value, 'during-refresh.example.test')
  assert.equal(textOf(descendants(ui.render(), node => node.type === 'dd')[0]), 'modeDirect')
  assert.match(ui.text(), /systemUnsupported.*errorPac/)
  assert.match(ui.text(), /refreshed/)
  ui.click('test')
  assert.deepEqual(ui.body(2), { mode: 'manual', url: full, bypass: ['during-refresh.example.test'] })
})

test('memory UI: refresh adopts a newer saved configuration only while the draft is pristine', async t => {
  const ui = await loaded(t)
  ui.click('refresh')
  ui.respond(1, { ...config(), mode: 'system', url: 'http://new-user:new-password@new-proxy.example.test:8888', bypass: ['new.example.test'] })
  await ui.flush()
  assert.equal(ui.mode('system').props.checked, true)
  assert.equal(ui.textarea().props.value, 'new.example.test')
  assert.doesNotMatch(ui.text(), /unsaved/)
  ui.selectMode('manual')
  assert.equal(ui.input('-url').props.value, 'http://new-proxy.example.test:8888')
  ui.click('save')
  assert.equal(Object.hasOwn(ui.body(2), 'url'), false)
})

test('memory UI: every system status and its blocking behavior are localized without inventing a direct route', async t => {
  for (const language of ['en', 'zh'] as const) {
    for (const [status, label] of [['ready', 'systemReady'], ['direct', 'systemDirect'], ['unsupported', 'systemUnsupported'], ['error', 'systemError']] as const) {
      const fixture = config()
      fixture.mode = 'system'
      fixture.system.status = status
      if (status === 'direct') fixture.system.httpProxy = ''
      if (status === 'unsupported') fixture.system.error = 'system-proxy-socks-unsupported'
      if (status === 'error') fixture.system.error = 'system-proxy-read-failed'
      const ui = await loaded(t, fixture, language)
      assert.ok(ui.text().includes(ui.translate(label)))
      if (status === 'unsupported' || status === 'error') {
        assert.ok(ui.text().includes(ui.translate('effectiveUnavailable')))
        assert.equal(ui.text().includes(ui.translate('httpProxy')), false)
        assert.equal(ui.text().includes(ui.translate('httpsProxy')), false)
      } else {
        assert.ok(ui.text().includes(ui.translate('httpProxy')))
        assert.ok(ui.text().includes(ui.translate('httpsProxy')))
      }
    }
  }
})

test('memory UI: HTTP errors, malformed JSON/schema and invalid test metadata surface safe errors and allow retry', async t => {
  const ui = await loaded(t)
  const cases: { status: number; payload?: unknown; key: string; invalidJson?: boolean }[] = [
    { status: 401, payload: { error: 'fixture-password' }, key: 'errorUnauthorized' },
    { status: 403, payload: { error: 'fixture-password' }, key: 'errorUnauthorized' },
    { status: 500, payload: {}, key: 'errorHttp' },
    { status: 400, payload: { ok: false, error: 'proxy-auth' }, key: 'errorProxyAuth' },
    { status: 200, payload: { ok: false, error: 'fixture-password' }, key: 'errorUnknown' },
    { status: 200, payload: null, key: 'errorResponse' },
    { status: 200, payload: { ok: 'true' }, key: 'errorResponse' },
    { status: 200, payload: { ok: true, route: 'fallback' }, key: 'errorResponse' },
    { status: 200, payload: { ok: true, origin: 42 }, key: 'errorResponse' },
    { status: 200, payload: { ok: true, origin: 'socks5://fixture-password@proxy.example.test' }, key: 'errorResponse' },
    { status: 200, key: 'errorResponse', invalidJson: true },
    { status: 502, key: 'errorHttp', invalidJson: true },
  ]
  for (const entry of cases) {
    ui.click('test')
    const index = ui.requests.length - 1
    if (entry.invalidJson) ui.requests[index]!.resolve({ status: entry.status, ok: entry.status === 200, json: async () => { throw new SyntaxError('raw fixture-password') } })
    else ui.respond(index, entry.payload, entry.status)
    await ui.flush()
    assert.match(ui.text(), new RegExp(`testFailed.*${entry.key}`))
    assert.equal(ui.text().includes('fixture-password'), false)
    assert.equal(ui.button('test').props.disabled, false)
  }
  ui.click('refresh')
  ui.requests.at(-1)!.resolve({ status: 200, ok: true, json: async () => { throw new SyntaxError('raw fixture-password') } })
  await ui.flush()
  assert.match(ui.text(), /refreshFailed.*errorResponse/)
  assert.match(ui.text(), /stale/)
  assert.equal(ui.input('-url').props.value, config().url)
})

test('memory UI: network rejection and request timeout release busy state without leaking exceptions', async t => {
  const ui = harness(t, 'keys', true)
  ui.requests[0]!.reject(new Error('fetch failed http://fixture-user:fixture-password@proxy.example.test'))
  await ui.flush()
  assert.match(ui.text(), /loadFailed.*errorNetwork/)
  assert.equal(ui.text().includes('fixture-password'), false)
  assert.equal(ui.timerCount, 0)
  ui.click('refresh')
  ui.respond(1, config())
  await ui.flush()
  ui.click('test')
  ui.fireTimeout()
  await ui.flush()
  assert.equal(ui.requests[2]!.options.signal.aborted, true)
  assert.match(ui.text(), /testFailed.*errorTimeout/)
  assert.equal(ui.button('test').props.disabled, false)
  assert.equal(ui.timerCount, 0)
})

test('memory UI: invalid initial config cannot enable mutations; a successful refresh recovers', async t => {
  const invalidViews = [
    { ok: true, mode: 'bogus' },
    { ...config(), hasCredentials: 'yes' },
    { ...config(), bypass: [null] },
    { ...config(), system: { ...config().system, status: 'bogus' } },
    { ...config(), url: 'ftp://fixture-user:fixture-password@proxy.example.test' },
  ]
  for (const payload of invalidViews) {
    const ui = harness(t)
    ui.respond(0, payload)
    await ui.flush()
    assert.match(ui.text(), /loadFailed.*errorResponse/)
    assert.equal(ui.button('save').props.disabled, true)
    assert.equal(ui.button('test').props.disabled, true)
    assert.equal(ui.button('refresh').props.disabled, false)
    assert.equal(ui.text().includes('fixture-password'), false)
    ui.click('refresh')
    ui.respond(1, config())
    await ui.flush()
    assert.equal(ui.button('save').props.disabled, false)
    assert.equal(ui.input('-url').props.value, config().url)
  }
})

test('memory UI: unmount aborts GET/save/test/refresh and ignores late resolution or rejection', async t => {
  for (const operation of ['load', 'save', 'test', 'refresh']) {
    for (const outcome of ['resolve', 'reject']) {
      const ui = operation === 'load' ? harness(t) : await loaded(t)
      if (operation !== 'load') ui.click(operation)
      const index = ui.requests.length - 1
      ui.unmount()
      assert.equal(ui.requests[index]!.options.signal.aborted, true)
      assert.equal(ui.timerCount, 0)
      if (outcome === 'resolve') ui.respond(index, operation === 'test' ? { ok: true, route: 'proxy' } : config())
      else ui.requests[index]!.reject(new Error('late fixture-password'))
      await ui.flush()
      assert.equal(ui.updatesAfterUnmount, 0)
    }
  }
})

test('memory UI: StrictMode-style effect replay ignores the abandoned initial request in either completion order', async t => {
  for (const oldFirst of [true, false]) {
    const ui = harness(t)
    ui.strictReplay()
    assert.equal(ui.requests.length, 2)
    assert.equal(ui.requests[0]!.options.signal.aborted, true)
    if (oldFirst) { ui.respond(0, config()); await ui.flush() }
    ui.respond(1, { ...config(), mode: 'direct' })
    await ui.flush()
    if (!oldFirst) { ui.respond(0, config()); await ui.flush() }
    assert.equal(ui.mode('direct').props.checked, true)
    assert.equal(ui.button('save').props.disabled, false)
    assert.equal(ui.timerCount, 0)
  }
})
