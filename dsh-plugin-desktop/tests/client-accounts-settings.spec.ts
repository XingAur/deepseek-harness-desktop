// @vitest-environment jsdom
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DesktopAccountsSection, type DesktopAccountsSectionProps } from '../src/client/DesktopAccountsSection.tsx'
import { zh } from '../src/client/desktop-accounts-locales.ts'
// Pulls the LocaleNamespaceMap augmentation so PropsLocale<'desktop.accounts'>
// resolves with `t` under the tests project too.
import { DESKTOP_ACCOUNTS_LOCALE_NAMESPACE } from '../src/client/desktop-settings.ts'
import {
  createDesktopSettingsApi,
  desktopSettingsPaths,
  parseDesktopAccountsListView,
  type DesktopAccountsListView,
} from '../src/client/desktop-settings-api.ts'

void DESKTOP_ACCOUNTS_LOCALE_NAMESPACE

const LIST_BODY: DesktopAccountsListView = {
  available: true,
  providers: [
    {
      provider: 'xai',
      label: 'xAI',
      activeLabel: 'work',
      inFlight: false,
      saved: [{ id: 'xai-acct-1', label: 'private' }],
    },
    {
      provider: 'openai-codex',
      label: 'OpenAI Codex',
      activeLabel: null,
      inFlight: true,
      saved: [],
    },
  ],
}

interface RecordedRequest {
  readonly input: RequestInfo | URL
  readonly init: RequestInit | undefined
}

function fetcher(reply: (input: RequestInfo | URL, init: RequestInit | undefined) => unknown): {
  readonly fetch: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>
  readonly requests: readonly RecordedRequest[]
} {
  const requests: RecordedRequest[] = []
  const fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    requests.push({ input, init })
    return new Response(JSON.stringify(reply(input, init)), { status: 200 })
  }
  return { fetch, requests }
}

const MUTATION_BODY = { accepted: true, restartRecommended: false }

function routeBody(input: RequestInfo | URL): unknown {
  const path = String(input)
  if (path === desktopSettingsPaths.accountsList) return LIST_BODY
  return MUTATION_BODY
}

describe('desktop accounts client api', () => {
  it('keeps the strict same-origin routes and bodies', async () => {
    const { fetch, requests } = fetcher(routeBody)
    const api = createDesktopSettingsApi(fetch)
    expect(desktopSettingsPaths.accountsList).toBe('/api/desktop/accounts')
    expect(desktopSettingsPaths.accountsAdd).toBe('/api/desktop/accounts/add')
    expect(desktopSettingsPaths.accountsSwitch).toBe('/api/desktop/accounts/switch')
    expect(desktopSettingsPaths.accountsRemove).toBe('/api/desktop/accounts/remove')
    expect(desktopSettingsPaths.accountsLogout).toBe('/api/desktop/accounts/logout')
    await api.listAccounts()
    await api.addModelAccount('xai')
    await api.switchModelAccount('xai', 'xai-acct-1')
    await api.removeModelAccount('xai', 'xai-acct-1')
    await api.logoutModelAccount('xai')
    expect(requests.map(request => `${String(request.init?.method ?? 'GET')} ${String(request.input)}`)).toEqual([
      'GET /api/desktop/accounts',
      'POST /api/desktop/accounts/add',
      'POST /api/desktop/accounts/switch',
      'POST /api/desktop/accounts/remove',
      'POST /api/desktop/accounts/logout',
    ])
    expect(requests[1]?.init?.body).toBe(JSON.stringify({ provider: 'xai' }))
    expect(requests[2]?.init?.body).toBe(JSON.stringify({ provider: 'xai', account: 'xai-acct-1' }))
    expect(requests[3]?.init?.body).toBe(JSON.stringify({ provider: 'xai', account: 'xai-acct-1' }))
    expect(requests[4]?.init?.body).toBe(JSON.stringify({ provider: 'xai' }))
  })

  it('parses the bounded listing and mutation outcomes', async () => {
    const { fetch } = fetcher(routeBody)
    const api = createDesktopSettingsApi(fetch)
    expect(await api.listAccounts()).toEqual(LIST_BODY)
    expect(await api.addModelAccount('xai')).toEqual({ accepted: true, restartRecommended: false })
    const switched = fetcher(() => ({ accepted: true, restartRecommended: true }))
    const mutating = createDesktopSettingsApi(switched.fetch)
    expect(await mutating.switchModelAccount('xai', 'xai-acct-1'))
      .toEqual({ accepted: true, restartRecommended: true })
  })

  it('rejects malformed listings before they reach React state', () => {
    expect(() => parseDesktopAccountsListView({ available: true })).toThrow()
    expect(() => parseDesktopAccountsListView({ available: 'yes', providers: [] })).toThrow()
    expect(() => parseDesktopAccountsListView({
      available: true,
      providers: [{ provider: 'xai', label: 'xAI', activeLabel: null, inFlight: false, saved: [{ id: 'evil', label: 'x' }] }],
    })).toThrow()
    expect(() => parseDesktopAccountsListView({
      available: true,
      providers: [{ provider: 'xai', label: 'xAI', activeLabel: { token: 1 }, inFlight: false, saved: [] }],
    })).toThrow()
    expect(() => parseDesktopAccountsListView({
      available: true,
      providers: [{ provider: 'xai', label: 'xAI', activeLabel: null, inFlight: false, saved: [
        { id: 'xai-acct-1', label: 'ok', extra: true },
      ] }],
    })).toThrow()
  })

  it('surfaces non-2xx responses as failures', async () => {
    const api = createDesktopSettingsApi(async () =>
      await new Response(JSON.stringify({ error: 'unknown slot' }), { status: 404 }))
    await expect(api.switchModelAccount('xai', 'xai-acct-9')).rejects.toThrow()
  })
})

describe('accounts settings page interaction', () => {
  let root: Root | undefined
  let container: HTMLDivElement | undefined

  afterEach(() => {
    root?.unmount()
    container?.remove()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  async function mount(list: DesktopAccountsListView) {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    vi.useFakeTimers()
    const api = {
      listAccounts: vi.fn(async () => list),
      addModelAccount: vi.fn(async () => ({ accepted: true as const, restartRecommended: false })),
      switchModelAccount: vi.fn(async () => ({ accepted: true as const, restartRecommended: true })),
      removeModelAccount: vi.fn(async () => ({ accepted: true as const, restartRecommended: false })),
      logoutModelAccount: vi.fn(async () => ({ accepted: true as const, restartRecommended: true })),
    }
    const props = {
      t: (key: keyof typeof zh) => zh[key],
      api,
    } as unknown as DesktopAccountsSectionProps
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
    await act(async () => { root!.render(createElement(DesktopAccountsSection, props)) })
    return api
  }

  function button(label: string): HTMLButtonElement {
    const hit = [...container!.querySelectorAll('button')].find(item => item.textContent === label)
    if (hit === undefined) throw new Error(`button not found: ${label}`)
    return hit as HTMLButtonElement
  }

  it('lists the active account and saved slots, then switches on demand', async () => {
    const api = await mount(LIST_BODY)
    expect(container!.textContent).toContain('xAI')
    expect(container!.textContent).toContain('work')
    expect(container!.textContent).toContain('private')
    await act(async () => { button(zh.activateAccount).click() })
    expect(api.switchModelAccount).toHaveBeenCalledWith('xai', 'xai-acct-1')
    expect(container!.textContent).toContain(zh.switchedRestart)
  })

  it('shows a retry action when the listing request fails', async () => {
    vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
    const api = {
      listAccounts: vi.fn(async () => { throw new Error('offline') }),
    }
    const props = {
      t: (key: keyof typeof zh) => zh[key],
      api,
    } as unknown as DesktopAccountsSectionProps
    container = document.createElement('div')
    document.body.append(container)
    root = createRoot(container)
    await act(async () => { root!.render(createElement(DesktopAccountsSection, props)) })
    expect(container.textContent).toContain(zh.operationFailed)
    expect(container.textContent).toContain(zh.retry)
  })

  it('disables the add command while a sign-in is in flight', async () => {
    await mount(LIST_BODY)
    const codex = [...container!.querySelectorAll('section')]
      .find(section => section.textContent?.includes('OpenAI Codex'))
    if (codex === undefined) throw new Error('codex section not found')
    const add = [...codex.querySelectorAll('button')].find(item => item.textContent === zh.adding)
    expect(add?.disabled).toBe(true)
    expect(codex.textContent).toContain(zh.inFlight)
  })
})
