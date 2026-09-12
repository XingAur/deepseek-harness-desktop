// @vitest-environment jsdom
import { act, createElement } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DesktopProxySection, type DesktopProxySectionProps } from '../src/client/DesktopProxySection.tsx'
import { zh } from '../src/client/desktop-proxy-locales.ts'
// Pulls the LocaleNamespaceMap augmentation so PropsLocale<'desktop.proxy'>
// resolves with `t` under the tests project too.
import { DESKTOP_PROXY_LOCALE_NAMESPACE } from '../src/client/desktop-settings.ts'
import type { DesktopShellSettings } from '../src/client/DesktopSettingsSection.tsx'

void DESKTOP_PROXY_LOCALE_NAMESPACE

let root: Root | undefined
let container: HTMLDivElement | undefined

function scope() {
  const value: DesktopShellSettings = {
    mode: 'compatibility',
    port: 43_120,
    logLevel: 'info',
    openBrowser: false,
    networkExposure: 'loopback',
    macosMaterial: 'off',
    windowsMaterial: 'off',
    proxyUrl: '',
    modelProxyUrl: '',
    modelProxyProviders: ['xai', 'openai-codex'],
    modelProxyExtraHosts: [],
    modelProxyCustomProviders: [],
    modelProxyProviderUrls: [],
  }
  const snapshot = { status: 'ready', writable: true, value }
  return {
    getSnapshot: () => snapshot,
    subscribe: () => () => {},
    set: vi.fn(async () => {}),
  }
}

async function mount(scoped = scope()) {
  vi.stubGlobal('IS_REACT_ACT_ENVIRONMENT', true)
  container = document.createElement('div')
  document.body.append(container)
  root = createRoot(container)
  const props = {
    t: (key: keyof typeof zh) => zh[key],
    api: {},
    desktopSettings: scoped,
  } as unknown as DesktopProxySectionProps
  await act(async () => { root!.render(createElement(DesktopProxySection, props)) })
  return container
}

function button(label: string): HTMLButtonElement {
  const hit = [...container!.querySelectorAll('button')].find(item => item.textContent === label)
  if (hit === undefined) throw new Error(`button not found: ${label}`)
  return hit as HTMLButtonElement
}

function checkboxes(): HTMLInputElement[] {
  return [...container!.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')]
}

function urlInput(): HTMLInputElement {
  return [...container!.querySelectorAll<HTMLInputElement>('input[type="text"]')]
    .find(item => item.placeholder.includes('7890'))!
}

afterEach(() => {
  root?.unmount()
  container?.remove()
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('proxy settings page interaction', () => {
  it('keeps the form editable after a save: the restart notice never locks controls', async () => {
    vi.useFakeTimers()
    const scoped = scope()
    await mount(scoped)

    // Preset: route all vendors through the proxy.
    await act(async () => { button(zh.modelProxySelectOverseas).click() })
    expect(checkboxes().every(item => item.checked)).toBe(true)

    // Save succeeds; the restart notice state machine runs to its terminal state.
    await act(async () => {
      container!.querySelector('form')!.requestSubmit()
      await Promise.resolve()
    })
    await act(async () => { await vi.advanceTimersByTimeAsync(9_000) })
    expect(scoped.set).toHaveBeenCalled()
    expect(container!.textContent).toContain(zh.restartRequired)

    // Regression (v2.0.18): controls used to stay disabled forever after a save.
    expect(button(zh.modelProxyDirectAll).disabled).toBe(false)
    expect(urlInput().disabled).toBe(false)
    expect(button(zh.modelProxySave).disabled).toBe(false)

    // The form still accepts changes: clearing the group and typing a URL.
    await act(async () => { button(zh.modelProxyDirectAll).click() })
    expect(checkboxes().every(item => !item.checked)).toBe(true)
    await act(async () => {
      urlInput().value = 'http://127.0.0.1:7890'
      urlInput().dispatchEvent(new Event('input', { bubbles: true }))
    })
    expect(urlInput().value).toBe('http://127.0.0.1:7890')
  })
})
