import { Context } from '@deepseek-ai/cordis'
import { AuthorizationError } from '@deepseek-ai/dsh-authorization'
import type {
  AuthorizationEntry,
  AuthorizationRequest,
  AuthorizationService,
} from '@deepseek-ai/dsh-authorization'
import { credentialKey } from '@deepseek-ai/dsh-credentials'
import type { CredentialKey, CredentialProvider, CredentialRecord } from '@deepseek-ai/dsh-credentials'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { Readable } from 'node:stream'
import type { SettingsProvider } from '@deepseek-ai/dsh-settings'
import { describe, expect, it, vi } from 'vitest'
import DesktopModelAccountService, {
  DesktopAccountUnknownError,
  savedAccountKey,
} from '../src/desktop-model-accounts.ts'
import type { DesktopModelSigninBootstrap, DesktopModelSigninDialog } from '../src/desktop-model-signin.ts'
import DesktopSettingsController from '../src/desktop-settings-controller.ts'
import {
  handleDesktopAccountsAddRequest,
  handleDesktopAccountsListRequest,
  handleDesktopAccountsLogoutRequest,
  handleDesktopAccountsRemoveRequest,
  handleDesktopAccountsSwitchRequest,
} from '../src/desktop-settings-route.ts'

const XAI_KEY = credentialKey('llm-pi-ai', 'xai')
const CODEX_KEY = credentialKey('llm-pi-ai', 'openai-codex')
const ORIGIN = 'http://127.0.0.1:43120'

function grant(accountId: string): CredentialRecord {
  return { kind: 'grant', payload: { type: 'oauth', access: 'a', refresh: 'r', expires: 1, accountId } }
}

/** Structurally compatible stand-in for the optional authorization service. */
class FakeAuthorization {
  outcome: 'authorized' | 'cancelled' = 'authorized'
  failure: Error | undefined
  /** Runs when begin is called, before the outcome resolves; stands in for the OAuth commit. */
  onBegin: (() => void) | undefined

  constructor(readonly entries: readonly AuthorizationEntry[] = []) {}

  list(): readonly AuthorizationEntry[] {
    return this.entries
  }

  describe(key: CredentialKey): AuthorizationEntry | undefined {
    return this.entries.find(entry => entry.key === key)
  }

  async begin(_request: AuthorizationRequest): Promise<{ readonly status: string }> {
    this.onBegin?.()
    if (this.failure !== undefined) throw this.failure
    return { status: this.outcome }
  }
}

/** Structurally compatible stand-in for the credentials service. */
class FakeCredentials {
  readonly records = new Map<CredentialKey, CredentialRecord>()

  async readRecord(key: CredentialKey): Promise<CredentialRecord | undefined> {
    return this.records.get(key)
  }

  async listRecords(): Promise<readonly { readonly key: CredentialKey, readonly kind: CredentialRecord['kind'] }[]> {
    return [...this.records].map(([key, record]) => ({ key, kind: record.kind }))
  }

  async modifyRecord(
    key: CredentialKey,
    mutate: (current: CredentialRecord | undefined) => Promise<CredentialRecord | undefined>,
  ): Promise<CredentialRecord | undefined> {
    const next = await mutate(this.records.get(key))
    if (next !== undefined) this.records.set(key, next)
    return next ?? this.records.get(key)
  }

  async deleteRecord(key: CredentialKey): Promise<void> {
    this.records.delete(key)
  }
}

/** Structurally compatible stand-in for the optional settings service. */
class FakeSettings {
  section: unknown = {}
  readonly updates: object[] = []

  get(ns: string): unknown {
    return ns === 'llm-pi-ai' ? this.section : undefined
  }

  async update(ns: string, patch: object): Promise<void> {
    if (ns !== 'llm-pi-ai') throw new Error(`unexpected namespace ${ns}`)
    this.updates.push(patch)
  }
}

function bootstrap(dialogs: DesktopModelSigninDialog[]): DesktopModelSigninBootstrap {
  return {
    openExternal: vi.fn(),
    showDialog: async spec => {
      dialogs.push(spec)
      return 0
    },
    locale: () => 'zh',
  }
}

function xaiEntry(): AuthorizationEntry {
  return {
    key: XAI_KEY,
    label: 'xAI',
    methods: [{ id: 'oauth', label: 'Sign in with xAI' }],
    inFlight: false,
  }
}

interface MountOptions {
  readonly credentials?: FakeCredentials
  readonly authorization?: FakeAuthorization
  readonly settings?: FakeSettings
}

async function mount(options: MountOptions = {}): Promise<DesktopModelAccountService> {
  const ctx = new Context()
  if (options.credentials !== undefined) {
    ctx.provide('credentials', options.credentials as unknown as CredentialProvider)
  }
  if (options.authorization !== undefined) {
    ctx.provide('authorization', options.authorization as unknown as AuthorizationService)
  }
  if (options.settings !== undefined) {
    ctx.provide('settings', options.settings as unknown as SettingsProvider)
  }
  const fiber = ctx.plugin(DesktopModelAccountService, bootstrap([]))
  await fiber
  return ctx.desktopModelAccounts
}

describe('desktop model account library', () => {
  it('lists nothing when no credentials capability is mounted', async () => {
    const service = await mount()
    expect(await service.list()).toEqual([])
  })

  it('derives labels from the grant account id and falls back positionally', async () => {
    const credentials = new FakeCredentials()
    credentials.records.set(XAI_KEY, grant('acct_9'))
    credentials.records.set(CODEX_KEY, { kind: 'grant', payload: { type: 'oauth' } })
    credentials.records.set(savedAccountKey('xai', 1), grant('acct_2'))
    const service = await mount({ credentials })
    expect(await service.list()).toEqual([
      { provider: 'openai-codex', label: 'openai-codex', activeLabel: '当前账号', inFlight: false, saved: [] },
      { provider: 'xai', label: 'xai', activeLabel: 'acct_9', inFlight: false, saved: [
        { id: 'xai-acct-1', label: 'acct_2' },
      ] },
    ])
  })

  it('prefers the OAuth profile name and email over an opaque account id', async () => {
    const claims = {
      'https://api.openai.com/profile': { name: 'Ada', email: 'ada@example.com' },
    }
    const access = `x.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.x`
    const credentials = new FakeCredentials()
    credentials.records.set(CODEX_KEY, {
      kind: 'grant',
      payload: { type: 'oauth', access, accountId: 'opaque-id' },
    })
    const service = await mount({ credentials })
    expect((await service.list())[0]?.activeLabel).toBe('Ada <ada@example.com>')
  })

  it('does not list an unconfigured provider merely because authorization advertises it', async () => {
    const service = await mount({ authorization: new FakeAuthorization([xaiEntry()]), credentials: new FakeCredentials() })
    expect(await service.list()).toEqual([])
  })

  it('lists a configured OAuth provider as signed out without inventing an active account', async () => {
    const settings = new FakeSettings()
    settings.section = { providers: { xai: {} } }
    const service = await mount({
      settings,
      authorization: new FakeAuthorization([xaiEntry()]),
      credentials: new FakeCredentials(),
    })
    expect(await service.list()).toEqual([
      { provider: 'xai', label: 'xAI', activeLabel: null, inFlight: false, saved: [] },
    ])
  })

  it('makes the first added account active without shelving it', async () => {
    const dialogs: DesktopModelSigninDialog[] = []
    const credentials = new FakeCredentials()
    const authorization = new FakeAuthorization([xaiEntry()])
    const settings = new FakeSettings()
    const ctx = new Context()
    ctx.provide('credentials', credentials as unknown as CredentialProvider)
    ctx.provide('authorization', authorization as unknown as AuthorizationService)
    ctx.provide('settings', settings as unknown as SettingsProvider)
    credentials.records.set(CODEX_KEY, grant('other'))
    authorization.begin = async () => {
      credentials.records.set(XAI_KEY, grant('acct_1'))
      return { status: 'authorized' }
    }
    const fiber = ctx.plugin(DesktopModelAccountService, {
      openExternal: vi.fn(),
      showDialog: async spec => {
        dialogs.push(spec)
        return 0
      },
      locale: () => 'zh',
    })
    await fiber
    await ctx.desktopModelAccounts.add('xai')
    expect(credentials.records.get(XAI_KEY)).toEqual(grant('acct_1'))
    expect(credentials.records.has(savedAccountKey('xai', 1))).toBe(false)
    expect(dialogs[0]?.message).toBe('账号已添加并生效。')
    expect(settings.updates).toEqual([{ providers: { xai: {} } }])
  })

  it('shelves a committed account through add and restores the snapshot', async () => {
    const dialogs: DesktopModelSigninDialog[] = []
    const credentials = new FakeCredentials()
    const authorization = new FakeAuthorization([xaiEntry()])
    const settings = new FakeSettings()
    settings.section = { providers: { xai: {} } }
    const ctx = new Context()
    ctx.provide('credentials', credentials as unknown as CredentialProvider)
    ctx.provide('authorization', authorization as unknown as AuthorizationService)
    ctx.provide('settings', settings as unknown as SettingsProvider)
    credentials.records.set(XAI_KEY, grant('acct_1'))
    authorization.begin = async () => {
      credentials.records.set(XAI_KEY, grant('acct_2'))
      return { status: 'authorized' }
    }
    const fiber = ctx.plugin(DesktopModelAccountService, {
      openExternal: vi.fn(),
      showDialog: async spec => {
        dialogs.push(spec)
        return 0
      },
      locale: () => 'zh',
    })
    await fiber
    await ctx.desktopModelAccounts.add('xai')
    expect(credentials.records.get(XAI_KEY)).toEqual(grant('acct_1'))
    expect(credentials.records.get(savedAccountKey('xai', 1))).toEqual(grant('acct_2'))
    expect(dialogs[0]?.message).toBe('新账号已保存，原账号保持生效。')
    expect(settings.updates).toEqual([])
  })

  it('touches nothing when the attempt is cancelled', async () => {
    const dialogs: DesktopModelSigninDialog[] = []
    const credentials = new FakeCredentials()
    const authorization = new FakeAuthorization([xaiEntry()])
    const ctx = new Context()
    ctx.provide('credentials', credentials as unknown as CredentialProvider)
    ctx.provide('authorization', authorization as unknown as AuthorizationService)
    credentials.records.set(XAI_KEY, grant('acct_1'))
    authorization.outcome = 'cancelled'
    const fiber = ctx.plugin(DesktopModelAccountService, bootstrap(dialogs))
    await fiber
    await ctx.desktopModelAccounts.add('xai')
    expect(credentials.records.get(XAI_KEY)).toEqual(grant('acct_1'))
    expect(dialogs).toEqual([])
  })

  it('names an already-running attempt instead of surfacing it as a failure', async () => {
    const dialogs: DesktopModelSigninDialog[] = []
    const credentials = new FakeCredentials()
    const authorization = new FakeAuthorization([xaiEntry()])
    const ctx = new Context()
    ctx.provide('credentials', credentials as unknown as CredentialProvider)
    ctx.provide('authorization', authorization as unknown as AuthorizationService)
    authorization.failure = new AuthorizationError('busy', 'ALREADY_IN_FLIGHT')
    const fiber = ctx.plugin(DesktopModelAccountService, bootstrap(dialogs))
    await fiber
    await ctx.desktopModelAccounts.add('xai')
    expect(dialogs[0]?.message).toBe('该账号已有登录流程在进行中，请先完成或取消它。')
  })

  it('switches the active credential with the saved slot and demotes the previous one', async () => {
    const credentials = new FakeCredentials()
    credentials.records.set(XAI_KEY, grant('acct_1'))
    credentials.records.set(savedAccountKey('xai', 1), grant('acct_2'))
    const service = await mount({ credentials })
    const label = await service.switchTo('xai', 'xai-acct-1')
    expect(label).toBe('acct_2')
    expect(credentials.records.get(XAI_KEY)).toEqual(grant('acct_2'))
    expect(credentials.records.get(savedAccountKey('xai', 1))).toEqual(grant('acct_1'))
  })

  it('deletes the freed slot when switching without a previous active account', async () => {
    const credentials = new FakeCredentials()
    credentials.records.set(savedAccountKey('xai', 1), grant('acct_2'))
    const service = await mount({ credentials })
    await service.switchTo('xai', 'xai-acct-1')
    expect(credentials.records.get(XAI_KEY)).toEqual(grant('acct_2'))
    expect(credentials.records.has(savedAccountKey('xai', 1))).toBe(false)
  })

  it('fails loud on a switch naming an unknown slot', async () => {
    const service = await mount({ credentials: new FakeCredentials() })
    await expect(service.switchTo('xai', 'xai-acct-4')).rejects.toBeInstanceOf(DesktopAccountUnknownError)
    await expect(service.switchTo('openai-codex', 'xai-acct-1')).rejects.toBeInstanceOf(DesktopAccountUnknownError)
  })

  it('removes only the named saved slot', async () => {
    const credentials = new FakeCredentials()
    credentials.records.set(XAI_KEY, grant('acct_1'))
    credentials.records.set(savedAccountKey('xai', 1), grant('acct_2'))
    credentials.records.set(savedAccountKey('xai', 2), grant('acct_3'))
    const service = await mount({ credentials })
    const label = await service.remove('xai', 'xai-acct-1')
    expect(label).toBe('acct_2')
    expect(credentials.records.has(savedAccountKey('xai', 1))).toBe(false)
    expect(credentials.records.has(savedAccountKey('xai', 2))).toBe(true)
    expect(credentials.records.get(XAI_KEY)).toEqual(grant('acct_1'))
  })

  it('logs out only the active credential', async () => {
    const credentials = new FakeCredentials()
    credentials.records.set(XAI_KEY, grant('acct_1'))
    credentials.records.set(savedAccountKey('xai', 1), grant('acct_2'))
    const service = await mount({ credentials })
    await service.logout('xai')
    expect(credentials.records.has(XAI_KEY)).toBe(false)
    expect(credentials.records.has(savedAccountKey('xai', 1))).toBe(true)
  })

  it('refreshes the tray snapshot after mutations', async () => {
    const credentials = new FakeCredentials()
    credentials.records.set(XAI_KEY, grant('acct_1'))
    const ctx = new Context()
    ctx.provide('credentials', credentials as unknown as CredentialProvider)
    ctx.provide('authorization', new FakeAuthorization([xaiEntry()]) as unknown as AuthorizationService)
    const fiber = ctx.plugin(DesktopModelAccountService, bootstrap([]))
    await fiber
    const service = ctx.desktopModelAccounts
    await service.logout('xai')
    ctx.emit('credentials/record-updated', XAI_KEY)
    await new Promise(resolve => { setTimeout(resolve, 0) })
    expect(service.snapshotOf()).toEqual([])
  })
})

describe('desktop model account routes', () => {
  interface RouteHarness {
    readonly controller: DesktopSettingsController
    readonly credentials: FakeCredentials
  }

  async function routeHarness(): Promise<RouteHarness> {
    const credentials = new FakeCredentials()
    const ctx = new Context()
    ctx.provide('credentials', credentials as unknown as CredentialProvider)
    const fiber = ctx.plugin(DesktopModelAccountService, bootstrap([]))
    await fiber
    const service = ctx.desktopModelAccounts
    const controller = new DesktopSettingsController({
      profiles: {
        current: { name: 'desktop', dir: '/private/profiles/desktop' },
        list: () => [],
        create: () => { throw new Error('unused') },
        prepareSelection: async () => ({ restartRequired: true, restart: async () => {} }),
      },
      readMarket: () => ({ requested: 'disabled', effective: 'disabled', legacyDefaulted: false }),
      selectMarket: async () => ({ requested: 'disabled', effective: 'disabled', legacyDefaulted: false }),
      readWeb: () => ({
        localUrl: ORIGIN + '/',
        lanUrls: [],
        lanState: 'inactive',
        lanError: null,
        lanCaFingerprint: null,
        lanCaUrls: [],
      }),
      scheduleRestart: () => {},
      scheduleRecoveryRestart: () => {},
      openTerminal: () => {},
      reloadRenderer: () => {},
      toggleDeveloperTools: () => {},
      exportDiagnostics: async () => {},
      readAccounts: async () => ({ available: true, providers: [...await service.list()] }),
      addModelAccount: provider => { service.startAdd(provider) },
      switchModelAccount: async (provider, account) => await service.switchTo(provider, account),
      removeModelAccount: async (provider, account) => await service.remove(provider, account),
      logoutModelAccount: async provider => { await service.logout(provider) },
    })
    return { controller, credentials }
  }

  function jsonRequest(value: unknown, headers: Readonly<Record<string, string | undefined>> = {}): IncomingMessage {
    const body = JSON.stringify(value)
    const req = Readable.from([body]) as IncomingMessage
    req.method = 'POST'
    req.headers = {
      host: '127.0.0.1:43120',
      origin: ORIGIN,
      'sec-fetch-site': 'same-origin',
      'content-type': 'application/json; charset=utf-8',
      'content-length': String(Buffer.byteLength(body)),
      ...headers,
    }
    Object.defineProperty(req, 'socket', { configurable: true, value: { remoteAddress: '127.0.0.1' } })
    return req
  }

  function getRequest(): IncomingMessage {
    const req = Readable.from([]) as IncomingMessage
    req.method = 'GET'
    req.headers = { host: '127.0.0.1:43120', origin: ORIGIN, 'sec-fetch-site': 'same-origin' }
    Object.defineProperty(req, 'socket', { configurable: true, value: { remoteAddress: '127.0.0.1' } })
    return req
  }

  function response(): ServerResponse & { readonly body: string } {
    const res = {
      body: '',
      statusCode: 200,
      setHeader: vi.fn(),
      end: vi.fn((body?: string) => { res.body = body ?? '' }),
    }
    return res as unknown as ServerResponse & typeof res
  }

  it('lists accounts without exposing payloads', async () => {
    const { controller, credentials } = await routeHarness()
    credentials.records.set(XAI_KEY, grant('acct_1'))
    credentials.records.set(savedAccountKey('xai', 1), grant('acct_2'))
    const res = response()
    await handleDesktopAccountsListRequest(getRequest(), res, ORIGIN, controller)
    expect(res.statusCode).toBe(200)
    expect(JSON.parse(res.body)).toEqual({
      available: true,
      providers: [
        { provider: 'xai', label: 'xai', activeLabel: 'acct_1', inFlight: false, saved: [
          { id: 'xai-acct-1', label: 'acct_2' },
        ] },
      ],
    })
    expect(res.body).not.toContain('oauth')
    expect(res.body).not.toContain('refresh')
  })

  it('reports an unavailable capability without account methods', async () => {
    const controller = new DesktopSettingsController({
      profiles: {
        current: { name: 'desktop', dir: '/private/profiles/desktop' },
        list: () => [],
        create: () => { throw new Error('unused') },
        prepareSelection: async () => ({ restartRequired: true, restart: async () => {} }),
      },
      readMarket: () => ({ requested: 'disabled', effective: 'disabled', legacyDefaulted: false }),
      selectMarket: async () => ({ requested: 'disabled', effective: 'disabled', legacyDefaulted: false }),
      readWeb: () => ({
        localUrl: ORIGIN + '/',
        lanUrls: [],
        lanState: 'inactive',
        lanError: null,
        lanCaFingerprint: null,
        lanCaUrls: [],
      }),
      scheduleRestart: () => {},
      scheduleRecoveryRestart: () => {},
      openTerminal: () => {},
      reloadRenderer: () => {},
      toggleDeveloperTools: () => {},
      exportDiagnostics: async () => {},
    })
    const res = response()
    await handleDesktopAccountsListRequest(getRequest(), res, ORIGIN, controller)
    expect(JSON.parse(res.body)).toEqual({ available: false, providers: [] })
  })

  it('switches through the route and recommends a restart', async () => {
    const { controller, credentials } = await routeHarness()
    credentials.records.set(XAI_KEY, grant('acct_1'))
    credentials.records.set(savedAccountKey('xai', 1), grant('acct_2'))
    const res = response()
    await handleDesktopAccountsSwitchRequest(
      jsonRequest({ provider: 'xai', account: 'xai-acct-1' }), res, ORIGIN, controller)
    expect(res.statusCode).toBe(200)
    expect(JSON.parse(res.body)).toEqual({ accepted: true, restartRecommended: true })
    expect(credentials.records.get(XAI_KEY)).toEqual(grant('acct_2'))
  })

  it('answers 404 for an unknown slot and 400 for a malformed body', async () => {
    const { controller } = await routeHarness()
    const missing = response()
    await handleDesktopAccountsSwitchRequest(
      jsonRequest({ provider: 'xai', account: 'xai-acct-9' }), missing, ORIGIN, controller)
    expect(missing.statusCode).toBe(404)
    const malformed = response()
    await handleDesktopAccountsSwitchRequest(jsonRequest({ provider: 'xai' }), malformed, ORIGIN, controller)
    expect(malformed.statusCode).toBe(400)
    const foreign = response()
    await handleDesktopAccountsSwitchRequest(
      jsonRequest({ provider: 'openai-codex', account: 'xai-acct-1' }), foreign, ORIGIN, controller)
    expect(foreign.statusCode).toBe(404)
  })

  it('rejects mutations from off-loopback origins', async () => {
    const { controller } = await routeHarness()
    const res = response()
    await handleDesktopAccountsLogoutRequest(
      jsonRequest({ provider: 'xai' }, { origin: 'http://evil.example:43120' }), res, ORIGIN, controller)
    expect(res.statusCode).toBe(403)
  })

  it('starts an add in the background and logs out with a restart recommendation', async () => {
    const { controller, credentials } = await routeHarness()
    const add = response()
    await handleDesktopAccountsAddRequest(jsonRequest({ provider: 'xai' }), add, ORIGIN, controller)
    expect(JSON.parse(add.body)).toEqual({ accepted: true, restartRecommended: false })
    credentials.records.set(XAI_KEY, grant('acct_1'))
    const logout = response()
    await handleDesktopAccountsLogoutRequest(jsonRequest({ provider: 'xai' }), logout, ORIGIN, controller)
    expect(JSON.parse(logout.body)).toEqual({ accepted: true, restartRecommended: true })
    expect(credentials.records.has(XAI_KEY)).toBe(false)
  })

  it('removes through the route without recommending a restart', async () => {
    const { controller, credentials } = await routeHarness()
    credentials.records.set(savedAccountKey('xai', 1), grant('acct_2'))
    const res = response()
    await handleDesktopAccountsRemoveRequest(
      jsonRequest({ provider: 'xai', account: 'xai-acct-1' }), res, ORIGIN, controller)
    expect(JSON.parse(res.body)).toEqual({ accepted: true, restartRecommended: false })
    expect(credentials.records.has(savedAccountKey('xai', 1))).toBe(false)
  })
})
