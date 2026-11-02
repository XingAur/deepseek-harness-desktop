import assert from 'node:assert/strict'
import { mkdtemp, readFile, readdir, rm, stat, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import test from 'node:test'
import { parse, stringify } from 'yaml'
import {
  modelProxyConfigView, parseModelProxyConfig, parseModelProxyUrl, proxyDiagnosticOrigin,
  readModelProxyConfig, writeModelProxyConfig,
} from '../src/model-proxy.ts'
import {
  createModelProxySnapshot, isLoopbackHost, matchesProxyBypass, parseProxyBypass,
  resolveModelProxyRoute, SystemProxyError,
} from '../src/model-proxy-routing.ts'
import { parseScutilProxyOutput, parseWindowsIeProxyOutput, readSystemProxy } from '../src/system-proxy.ts'
import type { SystemProxySnapshot } from '../src/system-proxy.ts'

const directSystem: SystemProxySnapshot = { status: 'direct', httpProxy: '', httpsProxy: '', bypass: [] }
const proxySystem: SystemProxySnapshot = {
  status: 'ready', httpProxy: 'http://proxy-http.invalid:8123', httpsProxy: 'http://proxy-https.invalid:8456',
  bypass: ['*.internal.test', '169.254.0.0/16'],
}
const secretProxy = 'https://fixture-user:fixture-password@proxy.invalid:8443'

// Synthetic documentation-range endpoints only. No OS commands or network calls.
function scutil(fields: string): string { return `<dictionary> {\n${fields}\n}\n` }
const staticScutil = scutil(`
  HTTPEnable : 1
  HTTPProxy : 192.0.2.40
  HTTPPort : 8123
  HTTPSEnable : 1
  HTTPSProxy : proxy.example.test
  HTTPSPort : 8456
  ProxyAutoConfigEnable : 0
  SOCKSEnable : 1
  SOCKSProxy : 192.0.2.40
  SOCKSPort : 8123
  ExceptionsList : <array> {
    0 : *.internal.test
    1 : 169.254/16
    2 : service-*.example.test
  }
  ExcludeSimpleHostnames : 1`)

function windowsIe(fields: Record<string, unknown> = {}): string {
  return JSON.stringify({
    schemaVersion: 1, ok: true, autoDetect: false, autoConfigUrl: '', proxy: '', proxyBypass: '',
    autoDetectFound: false, autoDetectError: '', ...fields,
  })
}

async function withHome(run: (home: string) => Promise<void>): Promise<void> {
  const parent = resolve(tmpdir())
  const home = await mkdtemp(join(parent, 'dsh-proxy-policy-fixture-'))
  try { await run(home) } finally {
    // Validate the absolute cleanup target, never remove a supplied home path.
    assert.equal(dirname(resolve(home)), parent)
    assert.ok(home.startsWith(join(parent, 'dsh-proxy-policy-fixture-')))
    await rm(home, { recursive: true, force: true })
  }
}

function snapshot(mode: 'system' | 'manual' | 'direct' = 'system', system = proxySystem, bypass: string[] = []) {
  return createModelProxySnapshot(parseModelProxyConfig({ mode, url: secretProxy, bypass }), system)
}

function errorCode(code: string) {
  return (error: unknown) => error instanceof Error && error.message === code
}

test('defaults to system and never interprets obsolete provider selection', () => {
  assert.deepEqual(parseModelProxyConfig({ providers: ['xai', 'openai-codex'] }), { mode: 'system', url: '', bypass: [] })
  assert.equal(parseModelProxyConfig({ url: secretProxy, providers: [] }).mode, 'system')
  for (const mode of ['', 'automatic', 1, null]) {
    assert.throws(() => parseModelProxyConfig({ mode }), errorCode('invalid-mode'))
  }
  for (const value of [null, [], 'manual']) assert.throws(() => parseModelProxyConfig(value), errorCode('invalid-config'))
})

test('POST-style patches preserve credentials unless the URL is explicitly replaced', () => {
  const before = parseModelProxyConfig({ mode: 'manual', url: secretProxy, bypass: ['Example.Test'] })
  const after = parseModelProxyConfig({ mode: 'system', bypass: ['corp.test'] }, before)
  assert.equal(after.url, secretProxy)
  assert.deepEqual(after.bypass, ['corp.test'])
  assert.equal(before.mode, 'manual')
  assert.deepEqual(before.bypass, ['example.test'])
  const cleared = parseModelProxyConfig({ mode: 'direct', url: '' }, after)
  assert.equal(cleared.url, '')
  assert.throws(() => parseModelProxyConfig({ mode: 'manual' }, cleared), errorCode('manual-url-required'))
  assert.ok(Object.isFrozen(after))
  assert.ok(Object.isFrozen(after.bypass))
})

test('proxy URLs reject paths, malformed authorities and non-HTTP schemes without echoing input', () => {
  for (const value of [undefined, '', '   ']) assert.equal(parseModelProxyUrl(value), '')
  assert.equal(parseModelProxyUrl(' http://proxy.test:8081 '), 'http://proxy.test:8081')
  assert.equal(parseModelProxyUrl('http://[2001:db8::1]:8081'), 'http://[2001:db8::1]:8081')
  assert.equal(parseModelProxyUrl(secretProxy), secretProxy)
  for (const url of [
    null, 42, 'garbage', 'http:proxy.test', 'http:///proxy.test', 'http://proxy.test:0', 'http://proxy.test:',
    'http://proxy.test:99999', 'http://proxy.test/api', 'http://proxy.test?token=secret',
    'http://proxy.test?', 'http://proxy.test#', 'http://proxy.test/#secret', 'http://user:secret@bad port',
    'http://proxy.test\\ignored', 'http://proxy.\ntest',
  ]) assert.throws(() => parseModelProxyUrl(url), errorCode('invalid-url'), String(url))
  assert.throws(() => parseModelProxyUrl('socks5://user:secret@proxy.test:1081'), errorCode('unsupported-proxy-scheme'))
  assert.throws(() => parseModelProxyUrl(`http://${'x'.repeat(2048)}.test`), errorCode('url-too-long'))
})

test('configuration views redact all proxy userinfo, including system endpoints', () => {
  const config = parseModelProxyConfig({ mode: 'manual', url: secretProxy })
  const view = modelProxyConfigView(config, { ...proxySystem, httpProxy: secretProxy, httpsProxy: 'http://only-password:secret@proxy.test' })
  assert.equal(view.url, 'https://proxy.invalid:8443')
  assert.equal(view.hasCredentials, true)
  assert.equal(view.system.httpProxy, 'https://proxy.invalid:8443')
  assert.equal(view.system.httpsProxy, 'http://proxy.test')
  assert.doesNotMatch(JSON.stringify(view), /fixture-user|fixture-password|only-password|secret@/)
  assert.equal(proxyDiagnosticOrigin('not a URL secret'), '')
  assert.equal(proxyDiagnosticOrigin(''), '')
  assert.equal(modelProxyConfigView(parseModelProxyConfig({}), directSystem).hasCredentials, false)
})

test('legacy URL migrates as manual candidate while default mode remains system', async () => {
  await withHome(async home => {
    await writeFile(join(home, 'settings.yaml'), stringify({ 'dsh-desktop': { modelProxyUrl: secretProxy, modelProxyProviders: ['xai'] } }))
    const legacyBefore = await readFile(join(home, 'settings.yaml'), 'utf8')
    assert.deepEqual(await readModelProxyConfig(home), { mode: 'system', url: secretProxy, bypass: [] })
    await writeModelProxyConfig(home, { mode: 'manual', bypass: ['example.test'] })
    assert.equal((await readModelProxyConfig(home)).url, secretProxy)
    assert.equal(await readFile(join(home, 'settings.yaml'), 'utf8'), legacyBefore)
    await writeModelProxyConfig(home, { mode: 'system', url: '', bypass: [] })
    assert.equal((await readModelProxyConfig(home)).url, '', 'explicit clear cannot revive a legacy password')
  })
})

test('atomic saves preserve non-proxy overlay settings and serialize partial updates', async () => {
  await withHome(async home => {
    const original = { customTheme: 'fixture', remoteRelayOrigin: 'https://relay.invalid', modelProxyProviders: ['obsolete'] }
    await writeFile(join(home, 'overlay-custom.yaml'), stringify(original))
    await writeModelProxyConfig(home, { mode: 'manual', url: secretProxy, bypass: [] })
    await Promise.all([
      writeModelProxyConfig(home, { bypass: ['first.test'] }),
      writeModelProxyConfig(home, { mode: 'direct' }),
    ])
    assert.deepEqual(await readModelProxyConfig(home), { mode: 'direct', url: secretProxy, bypass: ['first.test'] })
    const raw = parse(await readFile(join(home, 'overlay-custom.yaml'), 'utf8'))
    for (const [key, value] of Object.entries(original)) assert.deepEqual(raw[key], value)
    assert.deepEqual((await readdir(home)).sort(), ['overlay-custom.yaml'])
    if (process.platform !== 'win32') assert.equal((await stat(join(home, 'overlay-custom.yaml'))).mode & 0o777, 0o600)
  })
})

test('corrupt stored configuration does not turn into an apparently direct configuration', async () => {
  await withHome(async home => {
    await writeFile(join(home, 'overlay-custom.yaml'), 'modelProxyMode: manual\nmodelProxyUrl: ftp://secret@invalid\n')
    await assert.rejects(readModelProxyConfig(home), errorCode('unsupported-proxy-scheme'))
    await writeFile(join(home, 'overlay-custom.yaml'), 'broken: [\n')
    await assert.rejects(readModelProxyConfig(home), errorCode('config-read-failed'))
    await assert.rejects(writeModelProxyConfig(home, { mode: 'direct', url: '', bypass: [] }), errorCode('config-read-failed'))
    assert.equal(await readFile(join(home, 'overlay-custom.yaml'), 'utf8'), 'broken: [\n')
  })
})

test('bypass rules normalize domain, Unicode, IP, CIDR and port forms', () => {
  assert.deepEqual(parseProxyBypass([' EXAMPLE.Test ', 'example.test.', 'api.123.test', '例子.test']), ['example.test', 'api.123.test', 'xn--fsqu00a.test'])
  assert.deepEqual(parseProxyBypass(['169.254/16', '[2001:db8::1]:0443', '2001:db8::/32']), ['169.254.0.0/16', '[2001:db8::1]:443', '2001:db8::/32'])
  assert.deepEqual(parseProxyBypass(['<local>', 'api-*.test'], 'system'), ['<local>', 'api-*.test'])
  for (const rules of [null, 'example.test', [1], [''], ['bad host'], ['http://example.test'], ['user@host'], ['bad.test:'], ['bad.test:0'], ['bad.test:65536'], ['bad.test:abc'], ['example.test/path'], ['999.999.999.999'], ['10.0.0.0/33'], ['2001:db8::/129'], ['api-*.test'], ['<local>'], ['bad..test']]) {
    assert.throws(() => parseProxyBypass(rules), errorCode('invalid-bypass'), JSON.stringify(rules))
  }
})

test('plain user domains include subdomains but not lookalikes; ports use effective defaults', () => {
  for (const url of ['https://example.test', 'https://api.example.test', 'https://nested.api.example.test']) {
    assert.equal(matchesProxyBypass(url, ['example.test']), true)
  }
  for (const url of ['https://notexample.test', 'https://example.test.attacker.test']) {
    assert.equal(matchesProxyBypass(url, ['example.test']), false)
  }
  assert.equal(matchesProxyBypass('https://example.test', ['example.test:443']), true)
  assert.equal(matchesProxyBypass('https://example.test:8443', ['example.test:443']), false)
  assert.equal(matchesProxyBypass('wss://example.test', ['example.test:443']), true)
  assert.equal(matchesProxyBypass('ws://example.test', ['example.test:80']), true)
  assert.equal(matchesProxyBypass('https://api.example.test', ['example.test'], 'system'), false)
})

test('system wildcard, suffix, simple-host and IPv4/IPv6 networks match without DNS', () => {
  assert.equal(matchesProxyBypass('https://api.example.test', ['*.example.test'], 'system'), true)
  assert.equal(matchesProxyBypass('https://example.test', ['*.example.test'], 'system'), false)
  assert.equal(matchesProxyBypass('https://service-one.example.test', ['service-*.example.test'], 'system'), true)
  assert.equal(matchesProxyBypass('https://a.example.test', ['.example.test'], 'system'), true)
  assert.equal(matchesProxyBypass('http://printer', ['<local>'], 'system'), true)
  assert.equal(matchesProxyBypass('http://printer.test', ['<local>'], 'system'), false)
  assert.equal(matchesProxyBypass('http://192.0.2.3', ['<local>'], 'system'), false)
  assert.equal(matchesProxyBypass('http://169.254.40.8', ['169.254/16'], 'system'), true)
  assert.equal(matchesProxyBypass('http://169.253.40.8', ['169.254/16'], 'system'), false)
  assert.equal(matchesProxyBypass('https://[2001:db8:1::8]', ['2001:db8::/32']), true)
  assert.equal(matchesProxyBypass('https://[2001:db9::8]', ['2001:db8::/32']), false)
  assert.equal(matchesProxyBypass('https://[2001:db8::8]:9443', ['[2001:db8::8]:9443']), true)
  assert.equal(matchesProxyBypass('https://[2001:db8::8]', ['[2001:db8::8]:9443']), false)
})

test('loopback includes the entire IPv4 block and IPv4-mapped IPv6 but not lookalikes', () => {
  for (const host of ['localhost', 'api.localhost', 'LOCALHOST.', '127.0.0.1', '127.222.1.7', '::1', '[::1]', '::ffff:127.0.0.1', '::ffff:7fff:1']) {
    assert.equal(isLoopbackHost(host), true, host)
  }
  for (const host of ['localhost.attacker.test', 'notlocalhost', '128.0.0.1', '0.0.0.0', '192.0.2.1', '::', '::ffff:192.0.2.1', '2001:db8::1']) {
    assert.equal(isLoopbackHost(host), false, host)
  }
})

test('macOS HTTP and HTTPS settings are independent; coexisting SOCKS is not SOCKS-only', () => {
  const system = parseScutilProxyOutput(staticScutil)
  assert.equal(system.status, 'ready')
  assert.equal(system.httpProxy, 'http://192.0.2.40:8123')
  assert.equal(system.httpsProxy, 'http://proxy.example.test:8456')
  assert.deepEqual(system.bypass, ['*.internal.test', '169.254.0.0/16', 'service-*.example.test', '<local>'])
  assert.ok(Object.isFrozen(system))
  assert.ok(Object.isFrozen(system.bypass))
  const httpsOnly = parseScutilProxyOutput(scutil('HTTPEnable : 0\nHTTPProxy : stale.invalid\nHTTPPort : garbage\nHTTPSEnable : 1\nHTTPSProxy : 2001:db8::1\nHTTPSPort : 8456'))
  assert.equal(httpsOnly.status, 'ready')
  assert.equal(httpsOnly.httpProxy, '')
  assert.equal(httpsOnly.httpsProxy, 'http://[2001:db8::1]:8456')
  assert.equal(parseScutilProxyOutput(scutil('HTTPEnable : 0\nHTTPSEnable : 0')).status, 'direct')
  assert.equal(parseScutilProxyOutput('<dictionary> {}').status, 'direct')
})

test('macOS reads only scutil through an injectable runner; unknown platforms do not probe', async () => {
  const calls: unknown[] = []
  const system = await readSystemProxy({ platform: 'darwin', runner: async (command, args) => {
    calls.push([command, args])
    return staticScutil
  } })
  assert.equal(system.status, 'ready')
  assert.deepEqual(calls, [['/usr/sbin/scutil', ['--proxy']]])
  for (const platform of ['linux', 'freebsd', 'unknown']) {
    const unknown = await readSystemProxy({ platform, runner: async () => { throw new Error('must not run') } })
    assert.equal(unknown.status, 'unsupported')
    assert.equal(unknown.error, 'system-proxy-unsupported-platform')
  }
})

test('Windows reads current-user WinHTTP JSON through an injectable runner', async () => {
  const calls: unknown[] = []
  const system = await readSystemProxy({
    platform: 'win32',
    runner: async (command, args) => {
      calls.push([command, ...args.slice(0, 4)])
      return windowsIe({ proxy: '192.0.2.10:8080', proxyBypass: '<local>;*.corp.test' })
    },
  })
  assert.equal(system.status, 'ready')
  assert.equal(system.httpProxy, 'http://192.0.2.10:8080')
  assert.equal(system.httpsProxy, 'http://192.0.2.10:8080')
  assert.deepEqual(system.bypass, ['<local>', '*.corp.test'])
  assert.match(String(calls[0]?.[0]), /powershell\.exe$/i)
  assert.deepEqual(calls[0]?.slice(1, 4), ['-NoProfile', '-NonInteractive', '-WindowStyle'])
  assert.equal(calls.length, 1)
})

test('Windows static HTTP(S) mappings, PAC, AutoDetect and bypass stay fail-closed', () => {
  assert.equal(parseWindowsIeProxyOutput(windowsIe()).status, 'direct')
  const split = parseWindowsIeProxyOutput(windowsIe({ proxy: 'http=192.0.2.10:8080;https=proxy.example.test:8443' }))
  assert.equal(split.status, 'ready')
  assert.equal(split.httpProxy, 'http://192.0.2.10:8080')
  assert.equal(split.httpsProxy, 'http://proxy.example.test:8443')
  const httpsOnly = parseWindowsIeProxyOutput(windowsIe({ proxy: 'https=192.0.2.10:8080' }))
  assert.equal(httpsOnly.httpProxy, '')
  assert.equal(httpsOnly.httpsProxy, 'http://192.0.2.10:8080')
  const ipv6 = parseWindowsIeProxyOutput(windowsIe({ proxy: '[2001:db8::10]:8080' }))
  assert.equal(ipv6.httpsProxy, 'http://[2001:db8::10]:8080')
  assert.equal(parseWindowsIeProxyOutput(windowsIe({ autoConfigUrl: 'http://wpad.example.test/proxy.pac' })).error, 'system-proxy-pac-unsupported')
  assert.equal(parseWindowsIeProxyOutput(windowsIe({
    autoDetect: true, autoDetectFound: true, autoDetectError: '',
  })).error, 'system-proxy-autodiscovery-unsupported')
  assert.equal(parseWindowsIeProxyOutput(windowsIe({
    autoDetect: true, autoDetectFound: false, autoDetectError: 'not-found',
  })).status, 'direct')
  const recovered = parseWindowsIeProxyOutput(windowsIe({
    autoDetect: true, autoDetectFound: false, autoDetectError: 'not-found', proxy: '192.0.2.10:8080',
  }))
  assert.equal(recovered.status, 'ready')
  assert.equal(recovered.httpProxy, 'http://192.0.2.10:8080')
  assert.equal(parseWindowsIeProxyOutput(windowsIe({
    autoDetect: true, autoDetectFound: false, autoDetectError: 'error',
  })).error, 'system-proxy-read-failed')
  assert.equal(parseWindowsIeProxyOutput(windowsIe({ ok: false })).error, 'system-proxy-read-failed')
  assert.equal(parseWindowsIeProxyOutput(windowsIe({ proxy: 'socks=192.0.2.10:1080' })).error, 'system-proxy-socks-unsupported')
  const socksAndHttp = parseWindowsIeProxyOutput(windowsIe({ proxy: 'http=192.0.2.10:8080;https=192.0.2.10:8080;socks=192.0.2.10:1080' }))
  assert.equal(socksAndHttp.status, 'ready')
  assert.equal(parseWindowsIeProxyOutput(windowsIe({ proxy: '192.0.2.10:8080 192.0.2.11:8080' })).error, 'system-proxy-list-unsupported')
  assert.equal(parseWindowsIeProxyOutput(windowsIe({ proxy: 'https://192.0.2.10:8080' })).error, 'system-proxy-list-unsupported')
  assert.equal(parseWindowsIeProxyOutput(windowsIe({ proxy: 'http://user:secret@192.0.2.10:8080' })).error, 'system-proxy-invalid-config')
  assert.equal(parseWindowsIeProxyOutput(windowsIe({ proxyBypass: '<-loopback>' })).error, 'system-proxy-bypass-unsupported')
  assert.doesNotMatch(JSON.stringify(parseWindowsIeProxyOutput(windowsIe({ proxy: 'http://user:secret@192.0.2.10:8080' }))), /secret/)
})

test('Windows read errors discard command output and do not treat environment proxies as system settings', async () => {
  const failure = await readSystemProxy({
    platform: 'win32',
    runner: async () => { throw new Error(`secret stdout ${secretProxy}`) },
  })
  assert.equal(failure.status, 'error')
  assert.equal(failure.error, 'system-proxy-read-failed')
  assert.doesNotMatch(JSON.stringify(failure), /fixture-password|secret stdout/)
  const parsed = parseWindowsIeProxyOutput(windowsIe())
  assert.equal(parsed.status, 'direct')
  assert.equal(parsed.httpProxy, '')
})

test('macOS read errors discard arbitrary stdout/stderr and credentials', async () => {
  const failure = await readSystemProxy({ platform: 'darwin', runner: async () => { throw new Error(`secret stdout ${secretProxy}`) } })
  assert.deepEqual(failure, { ...directSystem, status: 'error', error: 'system-proxy-read-failed' })
  assert.doesNotMatch(JSON.stringify(failure), /fixture-password|secret stdout/)
})

test('malformed or incomplete macOS settings are errors, not direct', () => {
  for (const output of [
    '', 'not scutil output', '<dictionary> {\nHTTPEnable : 1',
    scutil('HTTPEnable : 1'), scutil('HTTPEnable : 2'), scutil('HTTPEnable : 1\nHTTPProxy : proxy.test\nHTTPPort : 0'),
    scutil('HTTPEnable : 1\nHTTPProxy : proxy.test\nHTTPPort : 70000'),
    scutil('HTTPEnable : 1\nHTTPProxy : user:secret@proxy.test\nHTTPPort : 8123'),
    scutil('HTTPEnable : 1\nHTTPProxy : proxy.test/path\nHTTPPort : 8123'),
    scutil('HTTPEnable : 1\nHTTPProxy : [2001:db8::1\nHTTPPort : 8123'),
    scutil('HTTPEnable : 1\nHTTPEnable : 0'),
    scutil('ExceptionsList : <array> {\n0 : invalid/path\n}'),
  ]) {
    const result = parseScutilProxyOutput(output)
    assert.equal(result.status, 'error', output)
    assert.equal(result.error, 'system-proxy-invalid-config')
  }
})

test('PAC, auto discovery, SOCKS-only and destination-scoped settings are explicitly unsupported', () => {
  const cases = [
    ['ProxyAutoConfigEnable : 1\nProxyAutoConfigURLString : https://user:secret@pac.test/script', 'system-proxy-pac-unsupported'],
    ['ProxyAutoDiscoveryEnable : 1', 'system-proxy-autodiscovery-unsupported'],
    ['SOCKSEnable : 1\nSOCKSProxy : 192.0.2.1\nSOCKSPort : 1081', 'system-proxy-socks-unsupported'],
    ['__SCOPED__ : <dictionary> {\nen0 : <dictionary> {\nHTTPEnable : 1\n}\n}', 'system-proxy-scoped-unsupported'],
    ['__SUPPLEMENTAL__ : <array> {\n0 : <dictionary> {\nHTTPEnable : 1\n}\n}', 'system-proxy-scoped-unsupported'],
  ]
  for (const [fields, code] of cases) {
    const result = parseScutilProxyOutput(scutil(fields!))
    assert.equal(result.status, 'unsupported')
    assert.equal(result.error, code)
    assert.doesNotMatch(JSON.stringify(result), /user:secret/)
  }
})

test('manual mode proxies all model hosts rather than a provider allowlist', () => {
  for (const url of ['https://api.x.ai', 'https://api.openai.com', 'https://api.deepseek.com', 'https://arbitrary-model.test', 'wss://socket.example.test']) {
    assert.deepEqual(resolveModelProxyRoute(snapshot('manual'), url), { kind: 'proxy', proxyUrl: secretProxy, reason: 'manual' })
  }
})

test('loopback and explicit user bypass remain direct even if system resolution fails', () => {
  for (const status of ['unsupported', 'error'] as const) {
    const broken = { ...directSystem, status, error: 'system-proxy-read-failed' }
    const scoped = snapshot('system', broken, ['intranet.test:443'])
    for (const target of ['http://127.1', 'http://127.222.1.1', 'http://[::1]', 'http://[::ffff:127.0.0.1]', 'http://service.localhost']) {
      assert.deepEqual(resolveModelProxyRoute(scoped, target), { kind: 'direct', reason: 'loopback' })
    }
    assert.deepEqual(resolveModelProxyRoute(scoped, 'https://api.intranet.test'), { kind: 'direct', reason: 'user-bypass' })
    assert.throws(() => resolveModelProxyRoute(scoped, 'https://external.test'), (error: unknown) => error instanceof SystemProxyError && error.code === 'system-proxy-read-failed')
  }
})

test('system proxy rules and protocol selections do not create cross-scheme fallback', () => {
  assert.deepEqual(resolveModelProxyRoute(snapshot(), 'https://model.test'), { kind: 'proxy', proxyUrl: proxySystem.httpsProxy, reason: 'system' })
  assert.deepEqual(resolveModelProxyRoute(snapshot(), 'http://model.test'), { kind: 'proxy', proxyUrl: proxySystem.httpProxy, reason: 'system' })
  assert.deepEqual(resolveModelProxyRoute(snapshot(), 'wss://model.test'), { kind: 'proxy', proxyUrl: proxySystem.httpsProxy, reason: 'system' })
  assert.deepEqual(resolveModelProxyRoute(snapshot(), 'ws://model.test'), { kind: 'proxy', proxyUrl: proxySystem.httpProxy, reason: 'system' })
  assert.deepEqual(resolveModelProxyRoute(snapshot(), 'https://model.internal.test'), { kind: 'direct', reason: 'system-bypass' })
  assert.deepEqual(resolveModelProxyRoute(snapshot('system', { ...proxySystem, httpsProxy: '' }), 'https://model.test'), { kind: 'direct', reason: 'system-no-proxy' })
  assert.deepEqual(resolveModelProxyRoute(snapshot('system', directSystem), 'https://model.test'), { kind: 'direct', reason: 'system-no-proxy' })
  assert.deepEqual(resolveModelProxyRoute(snapshot('manual'), 'https://model.internal.test'), { kind: 'direct', reason: 'system-bypass' }, 'manual mode inherits successfully read system exceptions')
  const unreadable = { ...proxySystem, status: 'error' as const, error: 'system-proxy-read-failed' }
  assert.deepEqual(resolveModelProxyRoute(snapshot('manual', unreadable), 'https://model.internal.test'), { kind: 'proxy', proxyUrl: secretProxy, reason: 'manual' }, 'manual mode cannot trust stale/failed system exceptions')
  assert.throws(() => resolveModelProxyRoute(snapshot('system', unreadable), 'https://model.internal.test'), errorCode('system-proxy-read-failed'))
})

test('explicit direct never consults a system failure or uses a proxy endpoint', () => {
  const bad = { ...proxySystem, status: 'error' as const, error: 'system-proxy-read-failed' }
  assert.deepEqual(resolveModelProxyRoute(snapshot('direct', bad), 'https://model.test'), { kind: 'direct', reason: 'explicit-direct' })
  assert.throws(() => resolveModelProxyRoute(snapshot(), 'ftp://secret@invalid.test'), errorCode('invalid-target-url'))
})

test('snapshots survive later edits and concurrent route decisions without sharing mutable arrays', async () => {
  const config = { mode: 'manual' as const, url: secretProxy, bypass: ['original.test'] }
  const system = { ...proxySystem, bypass: ['*.original.test'] }
  const before = createModelProxySnapshot(config, system)
  config.url = 'http://new-proxy.invalid:8123'
  config.bypass.push('changed.test')
  system.httpsProxy = 'http://changed-system.invalid:8123'
  system.bypass.push('*.changed.test')
  const after = createModelProxySnapshot(config, system)
  assert.ok(Object.isFrozen(before))
  assert.ok(Object.isFrozen(before.config))
  assert.ok(Object.isFrozen(before.system))
  assert.ok(Object.isFrozen(before.config.bypass))
  assert.ok(Object.isFrozen(before.system.bypass))
  const decisions = await Promise.all([
    Promise.resolve().then(() => resolveModelProxyRoute(before, 'https://changed.test')),
    Promise.resolve().then(() => resolveModelProxyRoute(after, 'https://changed.test')),
  ])
  assert.deepEqual(decisions[0], { kind: 'proxy', proxyUrl: secretProxy, reason: 'manual' })
  assert.deepEqual(decisions[1], { kind: 'direct', reason: 'user-bypass' })
  assert.equal(before.system.httpsProxy, proxySystem.httpsProxy)
  assert.deepEqual(before.system.bypass, ['*.original.test'])
})
