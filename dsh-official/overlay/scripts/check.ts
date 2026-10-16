import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { sameOriginMutatingRequest } from '../src/http.ts'
import { modelsEndpoint } from '../src/models-endpoint.ts'
import { foldPickerModels, readConfiguredModels, writeModelSelection } from '../src/model-selection.ts'
import { extractModelIds, readOAuthCredential } from '../src/live-models.ts'
import { isOauthLoginProvider, oauthCredentialKey } from '../src/oauth-providers.ts'
import { mcpClientConfig, projectOverlayMcpServer, readOverlayMcpServers, resolvedMcpArgs, writeOverlayMcpServers } from '../src/mcp-state.ts'
import { MCP_TEMPLATES, templateInitialValues, templateRequirementsMet } from '../src/client/mcp-templates.ts'
import { readOverlayConfigSync, writeOverlayConfig } from '../src/overlay-config.ts'
import { canonicalRelayOrigin, persistRelayOrigin } from '../src/relay-origin.ts'
import { RELAY_REQUEST_HEADER, sanitizedTunnelHeaders } from '../src/relay-tunnel.ts'

const overlayRoot = join(dirname(fileURLToPath(import.meta.url)), '..')

test('desktop dsh-app origin may mutate overlay APIs', () => {
  const allowed = sameOriginMutatingRequest({
    headers: { origin: 'dsh-app://app', host: '127.0.0.1:19387' },
  } as never)
  const blocked = sameOriginMutatingRequest({
    headers: { origin: 'https://evil.example', host: '127.0.0.1:19387' },
  } as never)
  assert.equal(allowed, true)
  assert.equal(blocked, false)
  assert.equal(sameOriginMutatingRequest({
    headers: { host: '127.0.0.1:19387' },
  } as never), true)
})

test('relay origin accepts https and loopback http', () => {
  assert.equal(canonicalRelayOrigin('') , null)
  assert.equal(canonicalRelayOrigin('http://example.com'), null)
  assert.equal(canonicalRelayOrigin('https://relay.example.com')?.origin, 'https://relay.example.com')
  assert.equal(canonicalRelayOrigin('http://127.0.0.1:8787')?.origin, 'http://127.0.0.1:8787')
})

test('MCP rows persist in overlay config while projections hide secrets', async () => {
  const home = mkdtempSync(join(tmpdir(), 'dsh-overlay-mcp-'))
  try {
    const servers = await writeOverlayMcpServers(home, [{
      id: 'desktop-mcp-demo', serverName: 'demo', transport: 'stdio', command: 'node',
      args: ['server.js'], env: { TOKEN: 'secret' }, disabled: false,
    }])
    assert.deepEqual(await readOverlayMcpServers(home), servers)
    assert.deepEqual(projectOverlayMcpServer(servers[0]!), {
      id: 'desktop-mcp-demo', serverName: 'demo', transport: 'stdio', command: 'node',
      args: ['server.js'], envKeys: ['TOKEN'], cwd: null, url: null, headerKeys: [], disabled: false,
    })
    assert.equal(JSON.stringify(projectOverlayMcpServer(servers[0]!)).includes('secret'), false)
    assert.deepEqual(mcpClientConfig(servers[0]!), {
      transport: 'stdio', serverName: 'demo', command: 'node', args: ['server.js'],
      env: { TOKEN: 'secret' }, cwd: '',
    })
    assert.deepEqual(resolvedMcpArgs({
      id: 'database', serverName: 'database', transport: 'stdio', command: 'npx',
      args: ['--user', '${DB_USER}', '--password=${DB_PASSWORD}'], env: { DB_USER: 'reader', DB_PASSWORD: 'secret' },
    }), ['--user', 'reader', '--password=secret'])
    await writeOverlayMcpServers(home, [{
      id: 'desktop-mcp-demo', serverName: 'demo', transport: 'stdio', command: 'node',
      env: { TOKEN: null, NEXT: 'new-secret' },
    }])
    assert.deepEqual((await readOverlayMcpServers(home))[0]?.env, { NEXT: 'new-secret' })
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('MCP templates include guided database and Yunxiao configurations', () => {
  assert.deepEqual(MCP_TEMPLATES.map(template => template.id), [
    'database', 'yunxiao', 'gitlab', 'playwright', 'filesystem', 'memory', 'http-generic',
  ])
  const database = MCP_TEMPLATES.find(template => template.id === 'database')!
  assert.deepEqual(database.fields[0]?.options?.map(option => option.value), [
    'postgresql', 'mysql', 'oracle', 'sqlserver', 'sqlite',
  ])
  const values = { ...templateInitialValues(database), host: 'db.local', user: 'reader', password: 'secret', database: 'app' }
  assert.equal(templateRequirementsMet(database, values), true)
  const postgres = database.build(values)
  assert.equal(postgres.transport, 'stdio')
  if (postgres.transport === 'stdio') {
    assert.deepEqual(postgres.env, { DSH_DB_USER: 'reader', DSH_DB_PASSWORD: 'secret' })
    assert.ok(postgres.args.includes('${DSH_DB_PASSWORD}'))
    assert.equal(postgres.args.includes('secret'), false)
  }
  const yunxiao = MCP_TEMPLATES.find(template => template.id === 'yunxiao')!
  assert.deepEqual(yunxiao.build({ token: 'token-value' }), {
    transport: 'streamable-http',
    url: 'https://openapi-rdc.aliyuncs.com/ai/mcp?toolsets=organization-management,project-management',
    headers: { Authorization: 'Bearer token-value' },
  })
})

test('relay origin persists in the overlay-owned settings file', async () => {
  const home = mkdtempSync(join(tmpdir(), 'dsh-overlay-relay-'))
  try {
    await writeOverlayConfig(home, { modelProxyUrl: 'http://proxy.example' })
    await persistRelayOrigin(home, 'https://relay.example.com')
    assert.deepEqual(readOverlayConfigSync(home), {
      modelProxyUrl: 'http://proxy.example',
      remoteRelayOrigin: 'https://relay.example.com',
    })
    assert.equal(existsSync(join(home, 'settings.yaml')), false)
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('relay replaces any caller-supplied internal trust marker', () => {
  assert.deepEqual(sanitizedTunnelHeaders([
    [RELAY_REQUEST_HEADER, 'spoofed'],
    ['accept', 'application/json'],
  ]), [['accept', 'application/json']])
})

test('models endpoint strips Anthropic-compat suffix', () => {
  assert.equal(modelsEndpoint('https://api.deepseek.com/anthropic'), 'https://api.deepseek.com/models')
  assert.equal(modelsEndpoint('https://api.deepseek.com/'), 'https://api.deepseek.com/models')
})

test('ships a bundled client.js the official desktop can load', () => {
  assert.equal(existsSync(join(overlayRoot, 'lib', 'client.js')), true)
  assert.equal(existsSync(join(overlayRoot, 'lib', 'index.js')), true)
  const pkg = JSON.parse(readFileSync(join(overlayRoot, 'package.json'), 'utf8')) as {
    readonly exports?: { readonly '.'?: string; readonly './client'?: string }
  }
  assert.equal(pkg.exports?.['.'], './lib/index.js')
  assert.equal(pkg.exports?.['./client'], './lib/client.js')
})

test('desktop settings expose Skills and MCP management', () => {
  const client = readFileSync(join(overlayRoot, 'src', 'client', 'SettingsSections.tsx'), 'utf8')
  const mcpClient = readFileSync(join(overlayRoot, 'src', 'client', 'McpSettingsSection.tsx'), 'utf8')
  const templates = readFileSync(join(overlayRoot, 'src', 'client', 'mcp-templates.ts'), 'utf8')
  const host = readFileSync(join(overlayRoot, 'src', 'skills-mcp.ts'), 'utf8')
  assert.match(client, /id: 'overlay-skills'/)
  assert.match(client, /id: 'overlay-mcp'/)
  assert.match(client, /\/api\/desktop\/skills/)
  assert.match(mcpClient, /\/api\/desktop\/mcp\/test/)
  assert.match(mcpClient, /exportJson/)
  assert.match(templates, /id: 'yunxiao'/)
  assert.match(templates, /id: 'database'/)
  assert.match(host, /path: SKILLS_PATH/)
  assert.match(host, /path: MCP_PATH/)
  assert.match(host, /mountOverlayMcpServers/)
})

test('ships a same-origin mobile page for the relay pair URL', () => {
  for (const name of ['mobile.html', 'mobile.js', 'mobile.css']) {
    assert.equal(existsSync(join(overlayRoot, 'public', name)), true, name)
  }
})

test('host relay mounts mobile API and static phone page', () => {
  const relay = readFileSync(join(overlayRoot, 'src', 'relay.ts'), 'utf8')
  assert.match(relay, /registerMobileApi/)
  assert.match(relay, /path: '\/mobile\/login'/)
  assert.match(relay, /authorizeIndex/)
  assert.match(relay, /\/mobile\/login\?token=/)
  assert.match(relay, /path: '\/mobile'/)
  assert.match(relay, /path: '\/api\/desktop\/remote\/origin'/)
  assert.match(relay, /approval\/request/)
})

test('phone page only calls routes the overlay host registers', () => {
  const api = readFileSync(join(overlayRoot, 'src', 'mobile-api.ts'), 'utf8')
  const page = readFileSync(join(overlayRoot, 'public', 'mobile.js'), 'utf8')
  const calls = [...page.matchAll(/\bcall\('([a-z-]+)'/g)].map(match => match[1])
  assert.ok(calls.length > 0)
  for (const name of new Set(calls)) {
    assert.match(api, new RegExp(`path: '/api/desktop/mobile/${name}'`), name)
  }
})

test('does not move the 2.0.x harness pin', () => {
  const row = execFileSync('git', ['ls-tree', 'HEAD', 'deepseek-harness'], {
    cwd: join(overlayRoot, '..', '..'),
    encoding: 'utf8',
  }).trim()
  assert.match(row, /^160000 commit 82a5fd61a7[0-9a-f]+\tdeepseek-harness$/u)
})

test('oauth login keys stay on the official llm-pi-ai record addresses', () => {
  assert.equal(isOauthLoginProvider('openai-codex'), true)
  assert.equal(isOauthLoginProvider('xai'), true)
  assert.equal(isOauthLoginProvider('zai'), false)
  assert.equal(oauthCredentialKey('openai-codex'), 'llm-pi-ai/openai-codex')
  assert.equal(oauthCredentialKey('xai'), 'llm-pi-ai/xai')
})

test('Codex keeps official Luna/Sol/Terra SKUs and drops thinking aliases', () => {
  const folded = foldPickerModels('openai-codex', [
    'gpt-5.6-luna', 'gpt-6-luna', 'gpt-6-sol', 'gpt-5-6-thinking', 'gpt-5-5-instant',
  ])
  const ids = folded.map(entry => entry.id)
  assert.ok(ids.includes('gpt-5.6-luna'))
  assert.ok(ids.includes('gpt-5.6-sol'))
  assert.ok(ids.includes('gpt-6-astra'))
  assert.ok(ids.includes('gpt-6-luna'))
  assert.equal(folded.find(entry => entry.id === 'gpt-5.6-luna')?.name, 'GPT-5.6 Luna')
  assert.equal(ids.includes('gpt-5.6-thinking'), false)
})

test('model selection roundtrips through the profile patch and keeps siblings', async () => {
  const home = mkdtempSync(join(tmpdir(), 'dsh-overlay-selection-'))
  try {
    assert.deepEqual(await readConfiguredModels(home, 'xai'), [])
    const saved = await writeModelSelection(home, 'xai', ['grok-4.6', 'grok-4.7'])
    assert.deepEqual(saved, ['grok-4.6', 'grok-4.7'])
    assert.deepEqual(await readConfiguredModels(home, 'xai'), ['grok-4.6', 'grok-4.7'])
    // A second write for another route keeps the first route intact.
    await writeModelSelection(home, 'openai-codex', ['gpt-6-luna'])
    assert.deepEqual(await readConfiguredModels(home, 'xai'), ['grok-4.6', 'grok-4.7'])
    assert.deepEqual(await readConfiguredModels(home, 'openai-codex'), ['gpt-6-luna'])
    // The patch file stays a YAML array the official loader can read.
    const patch = readFileSync(join(home, 'profiles', 'desktop', 'cordis.patch.yml'), 'utf8')
    assert.match(patch, /id: llm-pi-ai/)
    assert.match(patch, /grok-4\.7/)
    assert.equal(patch.match(/input:\n\s+- text\n\s+- image/gu)?.length, 3)
    // Invalid ids are rejected without touching the file.
    await assert.rejects(() => writeModelSelection(home, 'xai', ['bad id with spaces']))
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('model selection also merges the settings.yaml user layer on read', async () => {
  const home = mkdtempSync(join(tmpdir(), 'dsh-overlay-settings-'))
  try {
    await writeModelSelection(home, 'xai', ['grok-4.6'])
    writeFileSync(join(home, 'settings.yaml'), [
      'llm-pi-ai:',
      '  providers:',
      '    xai:',
      '      models:',
      '        - id: grok-4.9',
      ''].join('\n'))
    assert.deepEqual(await readConfiguredModels(home, 'xai'), ['grok-4.6', 'grok-4.9'])
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('live model payload parsing accepts data arrays and model maps', () => {
  assert.deepEqual(extractModelIds({ data: [{ id: 'b' }, { id: 'a' }, { no: 'id' }] }), ['a', 'b'])
  assert.deepEqual(extractModelIds({ models: { 'grok-4.7': {}, 'grok-4.3': {} } }), ['grok-4.3', 'grok-4.7'])
  assert.deepEqual(extractModelIds(null), [])
  assert.deepEqual(extractModelIds({ data: 'nope' }), [])
})

test('oauth credential reader finds tokens in the active home only', async () => {
  const home = mkdtempSync(join(tmpdir(), 'dsh-overlay-creds-'))
  try {
    assert.equal(await readOAuthCredential(home, 'xai'), null)
    writeFileSync(join(home, '.credentials.yaml'), [
      'records:',
      '  llm-pi-ai/xai:',
      '    kind: json',
      '    payload:',
      '      type: oauth',
      '      access: token-xai',
      '      refresh: refresh-xai',
      '  llm-pi-ai/openai-codex:',
      '    kind: json',
      '    payload:',
      '      credential:',
      '        access: token-codex',
      '      accountId: acct-1',
      ''].join('\n'))
    assert.equal((await readOAuthCredential(home, 'xai'))?.access, 'token-xai')
    const codex = await readOAuthCredential(home, 'openai-codex')
    assert.equal(codex?.access, 'token-codex')
    assert.equal(codex?.accountId, 'acct-1')
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('portable stage admits images without catalog preflight', () => {
  const prepare = readFileSync(join(overlayRoot, '..', 'scripts', 'prepare-portable-stage.mjs'), 'utf8')
  assert.match(prepare, /admits images without catalog modality preflight/)
  assert.match(prepare, /MODEL_DOES_NOT_SUPPORT_IMAGES/)
  assert.match(prepare, /hasImage \? 'session\/attachment-invalid' : 'session\/agent-busy'/)
})

test('host model routes cover live fetch and selection persistence', () => {
  const models = readFileSync(join(overlayRoot, 'src', 'models.ts'), 'utf8')
  assert.match(models, /path: '\/api\/desktop\/models\/live'/)
  assert.match(models, /path: '\/api\/desktop\/models\/selection'/)
  assert.match(models, /writeModelSelection/)
  assert.match(models, /fetchLiveModelIds/)
})

test('client extras render refresh, checkboxes, save, and a status dot', () => {
  const extras = readFileSync(join(overlayRoot, 'src', 'client', 'ProviderCardExtras.tsx'), 'utf8')
  assert.match(extras, /refreshLatest/)
  assert.match(extras, /type="checkbox"/)
  assert.match(extras, /saveSelection/)
  assert.match(extras, /dshOverlayDotOk/)
  assert.match(extras, /dshOverlayBtnPrimary/)
})

test('host oauth routes exist and never require the authorization service at load', () => {
  const host = readFileSync(join(overlayRoot, 'src/index.ts'), 'utf8')
  const oauth = readFileSync(join(overlayRoot, 'src/oauth.ts'), 'utf8')
  assert.match(host, /applyOverlayOauth/)
  assert.match(oauth, /path: '\/api\/desktop\/oauth\/begin'/)
  assert.match(oauth, /get\('authorization'\)/)
  assert.doesNotMatch(oauth, /export const inject/)
})

test('overlay does not import the official checkout tree', () => {
  const files = [
    'src/index.ts', 'src/oauth.ts', 'src/oauth-providers.ts', 'src/models.ts',
    'src/model-selection.ts', 'src/live-models.ts', 'src/model-proxy.ts', 'src/proxy-api.ts',
    'src/client/index.ts', 'src/client/ProviderCardExtras.tsx', 'src/client/ProxyFooter.tsx',
    'src/client/overlay-styles.ts',
  ]
  for (const file of files) {
    const text = readFileSync(join(overlayRoot, file), 'utf8')
    assert.doesNotMatch(text, /dsh-official\/deepseek-harness/)
    assert.doesNotMatch(text, /from '@deepseek-ai\/dsh-authorization'/)
  }
})

test('model proxy allowlists overseas hosts and keeps domestic APIs direct', async () => {
  const { hostNeedsProxy, parseModelProxyUrl, proxyDiagnosticOrigin, writeModelProxyConfig, readModelProxyConfig } =
    await import('../src/model-proxy.ts')
  assert.equal(hostNeedsProxy('api.x.ai', ['xai']), true)
  assert.equal(hostNeedsProxy('chatgpt.com', ['openai-codex']), true)
  assert.equal(hostNeedsProxy('api.deepseek.com', ['xai', 'openai-codex']), false)
  assert.equal(parseModelProxyUrl('http://127.0.0.1:8001'), 'http://127.0.0.1:8001')
  assert.throws(() => parseModelProxyUrl('ftp://bad'))
  assert.equal(proxyDiagnosticOrigin('http://user:secret@127.0.0.1:8001'), 'http://127.0.0.1:8001')
  const home = mkdtempSync(join(tmpdir(), 'dsh-overlay-proxy-'))
  try {
    await writeModelProxyConfig(home, { url: 'http://127.0.0.1:8001', providers: ['xai'] })
    const saved = await readModelProxyConfig(home)
    assert.equal(saved.url, 'http://127.0.0.1:8001')
    assert.deepEqual(saved.providers, ['xai'])
  } finally {
    rmSync(home, { recursive: true, force: true })
  }
})

test('proxy API and official-token stylesheet exist', () => {
  const api = readFileSync(join(overlayRoot, 'src', 'proxy-api.ts'), 'utf8')
  const css = readFileSync(join(overlayRoot, 'src', 'client', 'overlay-styles.ts'), 'utf8')
  const host = readFileSync(join(overlayRoot, 'src', 'index.ts'), 'utf8')
  assert.match(api, /path: '\/api\/desktop\/proxy\/config'/)
  assert.match(api, /path: '\/api\/desktop\/proxy\/test'/)
  assert.match(host, /applyOverlayProxyApi/)
  assert.match(css, /--dsw-alias-button-primary-fill/)
  assert.match(css, /--dsw-alias-state-success-primary/)
})

test('overlay stays off the 2.0.x Yarn workspace and Electron shell', () => {
  const root = JSON.parse(readFileSync(join(overlayRoot, '..', '..', 'package.json'), 'utf8')) as {
    readonly workspaces?: readonly string[]
  }
  assert.equal((root.workspaces ?? []).includes('dsh-official/overlay'), false)
  const sources = [
    readFileSync(join(overlayRoot, 'src', 'index.ts'), 'utf8'),
    readFileSync(join(overlayRoot, 'src', 'relay.ts'), 'utf8'),
    readFileSync(join(overlayRoot, 'src', 'client', 'index.ts'), 'utf8'),
    readFileSync(join(overlayRoot, 'src', 'oauth.ts'), 'utf8'),
    readFileSync(join(overlayRoot, 'src', 'model-proxy.ts'), 'utf8'),
  ].join('\n')
  assert.doesNotMatch(sources, /desktopRuntime|from 'electron'|from \"electron\"/)
})
