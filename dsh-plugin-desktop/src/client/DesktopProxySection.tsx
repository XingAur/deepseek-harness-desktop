/** Settings sidebar page for model-scoped and process-wide proxies. */

import { useCallback, useEffect, useState, useSyncExternalStore, type FormEvent, type ReactNode } from 'react'
import type { SettingsScope } from '@deepseek-ai/dsh-client-ui-settings/client'
import type { InjectFace, PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import type { DesktopProxyTestView, DesktopSettingsApi } from './desktop-settings-api.ts'
import type { DesktopShellSettings } from './DesktopSettingsSection.tsx'
import { normalizeDesktopModelProxyHost } from '../desktop-model-proxy.ts'
import { parseDesktopProxyUrl } from '../desktop-proxy-url.ts'

/** Registration-side business face for the proxy settings section. */
export interface DesktopProxySectionInjected {
  readonly api: DesktopSettingsApi
  readonly desktopSettings: SettingsScope<DesktopShellSettings>
}

/** Renderer-composed props for the proxy settings section. */
export type DesktopProxySectionProps =
  PropsRuntime<'settings.section'>
  & PropsLocale<'desktop.proxy'>
  & InjectFace<DesktopProxySectionInjected>

type BusyOperation = 'proxy' | 'proxy-detect' | 'proxy-test' | 'model-proxy'
type ProxyDetectStatus = 'idle' | 'found' | 'missing'
type RestartState = 'none' | 'restarting' | 'required'
type CustomModel = { readonly name: string, readonly host: string, readonly proxyUrl: string }
const BUILTIN_MODEL_PROXY_PROVIDERS = ['xai', 'openai-codex', 'anthropic', 'gemini'] as const

function useScope<T>(scope: SettingsScope<T>) {
  const subscribe = useCallback((listener: () => void) => scope.subscribe(listener), [scope])
  const snapshot = useCallback(() => scope.getSnapshot(), [scope])
  return useSyncExternalStore(subscribe, snapshot)
}

/** Render the Desktop proxy settings page. */
export function DesktopProxySection({ t, api, desktopSettings }: DesktopProxySectionProps): ReactNode {
  const desktop = useScope(desktopSettings)
  const [busy, setBusy] = useState<BusyOperation>()
  const [operationFailed, setOperationFailed] = useState(false)
  const [restart, setRestart] = useState<RestartState>('none')
  const [proxyDraft, setProxyDraft] = useState('')
  const [proxyInvalid, setProxyInvalid] = useState(false)
  const [modelProxyDraft, setModelProxyDraft] = useState('')
  const [modelProxyInvalid, setModelProxyInvalid] = useState(false)
  const [modelProxyDetect, setModelProxyDetect] = useState<ProxyDetectStatus>('idle')
  const [modelProxyProviders, setModelProxyProviders] = useState<readonly string[]>(['xai', 'openai-codex'])
  const [customModels, setCustomModels] = useState<readonly CustomModel[]>([])
  const [providerUrls, setProviderUrls] = useState<Record<string, string>>({})
  const [addName, setAddName] = useState('')
  const [addHost, setAddHost] = useState('')
  const [addProxyUrl, setAddProxyUrl] = useState('')
  const [customInvalid, setCustomInvalid] = useState(false)
  const [modelProxyTest, setModelProxyTest] = useState<DesktopProxyTestView>()

  useEffect(() => {
    if (desktop.status === 'ready' && desktop.value !== undefined) {
      setProxyDraft(desktop.value.proxyUrl ?? '')
      setProxyInvalid(false)
      setModelProxyDraft(desktop.value.modelProxyUrl ?? '')
      setModelProxyInvalid(false)
      setModelProxyProviders(desktop.value.modelProxyProviders ?? ['xai', 'openai-codex'])
      const custom = desktop.value.modelProxyCustomProviders ?? []
      if (custom.length > 0) {
        setCustomModels(custom.flatMap(item => {
          const host = item.hosts[0]
          return host === undefined ? [] : [{ name: item.name, host, proxyUrl: item.proxyUrl ?? '' }]
        }))
      } else {
        setCustomModels((desktop.value.modelProxyExtraHosts ?? []).map(host => ({ name: host, host, proxyUrl: '' })))
      }
      const urls: Record<string, string> = {}
      for (const row of desktop.value.modelProxyProviderUrls ?? []) urls[row.provider] = row.proxyUrl
      setProviderUrls(urls)
      setAddName('')
      setAddHost('')
      setAddProxyUrl('')
      setCustomInvalid(false)
      setModelProxyTest(undefined)
    }
  }, [desktop.status, desktop.value?.proxyUrl, desktop.value?.modelProxyUrl, desktop.value?.modelProxyProviders, desktop.value?.modelProxyExtraHosts, desktop.value?.modelProxyCustomProviders, desktop.value?.modelProxyProviderUrls])

  useEffect(() => {
    if (restart !== 'restarting') return
    const timer = setTimeout(() => { setRestart('required') }, 8_000)
    return () => { clearTimeout(timer) }
  }, [restart])

  const run = useCallback(async (operation: BusyOperation, invoke: () => Promise<void>) => {
    setBusy(operation)
    setOperationFailed(false)
    try {
      await invoke()
    } catch {
      setOperationFailed(true)
    } finally {
      setBusy(current => current === operation ? undefined : current)
    }
  }, [])

  const requestRestart = (): void => { setRestart('restarting') }
  const settingsWritable = desktop.status === 'ready' && desktop.writable
  const globalProxySet = proxyDraft.trim() !== ''
  const modelStatus = globalProxySet
    ? 'modelProxyStatusGlobal'
    : modelProxyDraft.trim() !== ''
      ? 'modelProxyStatusCustom'
      : 'modelProxyStatusEnv'

  const saveProxy = (event: FormEvent): void => {
    event.preventDefault()
    void run('proxy', async () => {
      let parsed: string
      try {
        parsed = parseDesktopProxyUrl(proxyDraft)
      } catch {
        setProxyInvalid(true)
        return
      }
      setProxyInvalid(false)
      await desktopSettings.set('proxyUrl', parsed)
      requestRestart()
    })
  }

  const saveModelProxy = (event: FormEvent): void => {
    event.preventDefault()
    void run('model-proxy', async () => {
      let parsed: string
      try {
        parsed = parseDesktopProxyUrl(modelProxyDraft)
      } catch {
        setModelProxyInvalid(true)
        return
      }
      setModelProxyInvalid(false)
      const extraHosts: string[] = []
      const customProviders: Array<{ name: string, hosts: string[], proxyUrl: string }> = []
      for (const item of customModels) {
        const host = normalizeDesktopModelProxyHost(item.host)
        if (host === undefined || item.name.trim() === '') {
          setCustomInvalid(true)
          return
        }
        let ownUrl = ''
        try {
          ownUrl = parseDesktopProxyUrl(item.proxyUrl)
        } catch {
          setCustomInvalid(true)
          return
        }
        if (!extraHosts.includes(host)) extraHosts.push(host)
        customProviders.push({ name: item.name.trim(), hosts: [host], proxyUrl: ownUrl })
      }
      const providerUrlRows: Array<{ provider: string, proxyUrl: string }> = []
      for (const [provider, raw] of Object.entries(providerUrls)) {
        if (!modelProxyProviders.includes(provider) || raw.trim() === '') continue
        try {
          providerUrlRows.push({ provider, proxyUrl: parseDesktopProxyUrl(raw) })
        } catch {
          setModelProxyInvalid(true)
          return
        }
      }
      setCustomInvalid(false)
      setModelProxyDetect('idle')
      setModelProxyTest(undefined)
      await desktopSettings.set('modelProxyUrl', parsed)
      await desktopSettings.set('modelProxyProviders', [...modelProxyProviders])
      await desktopSettings.set('modelProxyExtraHosts', extraHosts)
      await desktopSettings.set('modelProxyCustomProviders', customProviders)
      await desktopSettings.set('modelProxyProviderUrls', providerUrlRows)
      requestRestart()
    })
  }

  const TEST_LOCALE = {
    ok: 'modelProxyTestOk',
    'invalid-url': 'modelProxyTestInvalidUrl',
    'no-proxy': 'modelProxyTestNoProxy',
    'proxy-refused': 'modelProxyTestProxyRefused',
    'proxy-auth': 'modelProxyTestProxyAuth',
    timeout: 'modelProxyTestTimeout',
    tls: 'modelProxyTestTls',
    dns: 'modelProxyTestDns',
    'target-unreachable': 'modelProxyTestTargetUnreachable',
    unknown: 'modelProxyTestUnknown',
  } as const

  const testModelProxy = (): void => {
    void run('proxy-test', async () => {
      if (api.testModelProxy === undefined) return
      setModelProxyInvalid(false)
      setModelProxyDetect('idle')
      try {
        parseDesktopProxyUrl(modelProxyDraft)
      } catch {
        setModelProxyInvalid(true)
        return
      }
      const result = await api.testModelProxy(modelProxyDraft)
      setModelProxyTest(result)
    })
  }

  const detectModelProxy = (): void => {
    void run('proxy-detect', async () => {
      if (api.detectLocalProxy === undefined) return
      setModelProxyInvalid(false)
      const result = await api.detectLocalProxy()
      if (result.found) {
        setModelProxyDraft(result.proxyUrl)
        setModelProxyDetect('found')
        return
      }
      setModelProxyDetect('missing')
    })
  }

  const toggleModelProvider = (id: string, enabled: boolean): void => {
    setModelProxyProviders(current => {
      if (enabled) return current.includes(id) ? current : [...current, id]
      return current.filter(item => item !== id)
    })
  }

  const addCustomModel = (): void => {
    const name = addName.trim()
    const host = normalizeDesktopModelProxyHost(addHost)
    if (name === '' || host === undefined) {
      setCustomInvalid(true)
      return
    }
    if (customModels.some(item => item.name.toLowerCase() === name.toLowerCase() || item.host === host)) {
      setCustomInvalid(true)
      return
    }
    setCustomInvalid(false)
    let ownUrl = ''
    try {
      ownUrl = parseDesktopProxyUrl(addProxyUrl)
    } catch {
      setCustomInvalid(true)
      return
    }
    setCustomModels(current => [...current, { name, host, proxyUrl: ownUrl }])
    setAddName('')
    setAddHost('')
    setAddProxyUrl('')
  }

  return (
    <div className="dshDesktopSettings">
      <header className="dshDesktopSettingsHeader">
        <h2>{t('title')}</h2>
        <p>{t('intro')}</p>
      </header>

      {operationFailed && <p className="dshDesktopSettingsError" role="alert">{t('operationFailed')}</p>}
      {restart !== 'none' && (
        <p className="dshDesktopSettingsSuccess" role="status">
          {t(restart === 'restarting' ? 'restarting' : 'restartRequired')}
        </p>
      )}

      <section className="dshDesktopSettingsGroup" aria-labelledby="dsh-desktop-model-proxy-title">
        <div>
          <h3 id="dsh-desktop-model-proxy-title">{t('modelProxyTitle')}</h3>
          <p className="dshDesktopSettingsGroupIntro">{t('modelProxyIntro')}</p>
        </div>
        <p className="dshDesktopSettingsHint">{t(modelStatus)}</p>
        <form className="dshDesktopSettingsPanel dshDesktopSettingsProxyForm" onSubmit={saveModelProxy}>
          <div className="dshDesktopSettingsList" role="group" aria-labelledby="dsh-desktop-model-proxy-title">
            {BUILTIN_MODEL_PROXY_PROVIDERS.map(id => (
              <div key={id}>
                <label className="dshDesktopSettingsCheckRow">
                  <input
                    type="checkbox"
                    checked={modelProxyProviders.includes(id)}
                    disabled={!settingsWritable || busy !== undefined || restart !== 'none'}
                    onChange={event => { toggleModelProvider(id, event.currentTarget.checked) }}
                  />
                  <span>{t(id === 'openai-codex' ? 'modelProxyCodex' : id === 'anthropic' ? 'modelProxyAnthropic' : id === 'gemini' ? 'modelProxyGemini' : 'modelProxyXai')}</span>
                </label>
                {modelProxyProviders.includes(id) && (
                  <label className="dshDesktopSettingsField">
                    <span>{t('modelProxyOwnUrl')}</span>
                    <input
                      className="dshDesktopSettingsInput dshDesktopSettingsProxyInput"
                      type="text"
                      autoComplete="off"
                      placeholder={t('proxyUrlPlaceholder')}
                      value={providerUrls[id] ?? ''}
                      disabled={!settingsWritable || busy !== undefined || restart !== 'none'}
                      onChange={event => {
                        const value = event.currentTarget.value
                        setProviderUrls(current => ({ ...current, [id]: value }))
                      }}
                    />
                  </label>
                )}
              </div>
            ))}
            {customModels.map(item => (
              <label className="dshDesktopSettingsCheckRow" key={`${item.name}:${item.host}`}>
                <span>{item.name}</span>
                <button
                  type="button"
                  className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
                  disabled={!settingsWritable || busy !== undefined || restart !== 'none'}
                  onClick={() => {
                    setCustomModels(current => current.filter(entry => entry.name !== item.name || entry.host !== item.host))
                  }}
                >
                  {t('modelProxyRemove')}
                </button>
              </label>
            ))}
          </div>
          <div className="dshDesktopSettingsField">
            <span>{t('modelProxyExtraHosts')}</span>
            <p className="dshDesktopSettingsHint">{t('modelProxyExtraHostsHint')}</p>
            <label className="dshDesktopSettingsField">
              <span>{t('modelProxyAddName')}</span>
              <input
                className="dshDesktopSettingsInput dshDesktopSettingsProxyInput"
                type="text"
                autoComplete="off"
                value={addName}
                disabled={!settingsWritable || busy !== undefined || restart !== 'none'}
                onChange={event => {
                  setAddName(event.currentTarget.value)
                  setCustomInvalid(false)
                }}
              />
            </label>
            <label className="dshDesktopSettingsField">
              <span>{t('modelProxyAddHost')}</span>
              <input
                className="dshDesktopSettingsInput dshDesktopSettingsProxyInput"
                type="text"
                autoComplete="off"
                placeholder="api.anthropic.com"
                value={addHost}
                disabled={!settingsWritable || busy !== undefined || restart !== 'none'}
                onChange={event => {
                  setAddHost(event.currentTarget.value)
                  setCustomInvalid(false)
                }}
              />
            </label>
            <label className="dshDesktopSettingsField">
              <span>{t('modelProxyOwnUrl')}</span>
              <input
                className="dshDesktopSettingsInput dshDesktopSettingsProxyInput"
                type="text"
                autoComplete="off"
                placeholder={t('proxyUrlPlaceholder')}
                value={addProxyUrl}
                disabled={!settingsWritable || busy !== undefined || restart !== 'none'}
                onChange={event => {
                  setAddProxyUrl(event.currentTarget.value)
                  setCustomInvalid(false)
                }}
              />
            </label>
            <button
              type="button"
              className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
              disabled={!settingsWritable || busy !== undefined || restart !== 'none'}
              onClick={addCustomModel}
            >
              {t('modelProxyAdd')}
            </button>
          </div>
          {customInvalid && <p className="dshDesktopSettingsError" role="alert">{t('modelProxyExtraHostsInvalid')}</p>}
          <label className="dshDesktopSettingsField">
            <span>{t('proxyUrl')}</span>
            <input
              className="dshDesktopSettingsInput dshDesktopSettingsProxyInput"
              type="text"
              autoComplete="off"
              autoCapitalize="off"
              autoCorrect="off"
              spellCheck={false}
              value={modelProxyDraft}
              placeholder={t('proxyUrlPlaceholder')}
              disabled={!settingsWritable || busy !== undefined || restart !== 'none'}
              onChange={event => {
                setModelProxyDraft(event.currentTarget.value)
                setModelProxyInvalid(false)
                setModelProxyDetect('idle')
                setModelProxyTest(undefined)
              }}
            />
          </label>
          <div className="dshDesktopSettingsProxyActions">
            {api.detectLocalProxy !== undefined && (
              <button
                type="button"
                className="dshDesktopSettingsButton"
                disabled={!settingsWritable || busy !== undefined || restart !== 'none'}
                onClick={detectModelProxy}
              >
                {busy === 'proxy-detect' ? t('modelProxyDetecting') : t('modelProxyDetect')}
              </button>
            )}
            {api.testModelProxy !== undefined && (
              <button
                type="button"
                className="dshDesktopSettingsButton"
                disabled={!settingsWritable || busy !== undefined || restart !== 'none'}
                onClick={testModelProxy}
              >
                {busy === 'proxy-test' ? t('modelProxyTesting') : t('modelProxyTest')}
              </button>
            )}
            <button
              type="submit"
              className="dshDesktopSettingsPrimary"
              disabled={!settingsWritable || busy !== undefined || restart !== 'none'}
            >
              {t('modelProxySave')}
            </button>
          </div>
          {modelProxyInvalid && <p className="dshDesktopSettingsError" role="alert">{t('proxyInvalid')}</p>}
          {modelProxyDetect === 'found' && <p className="dshDesktopSettingsSuccess" role="status">{t('modelProxyDetected')}</p>}
          {modelProxyDetect === 'missing' && <p className="dshDesktopSettingsHint" role="status">{t('modelProxyDetectMissing')}</p>}
          {modelProxyTest !== undefined && (
            <p
              className={modelProxyTest.ok ? 'dshDesktopSettingsSuccess' : 'dshDesktopSettingsError'}
              role={modelProxyTest.ok ? 'status' : 'alert'}
            >
              {t(TEST_LOCALE[modelProxyTest.code])}
            </p>
          )}
        </form>
      </section>

      <details className="dshDesktopSettingsAdvanced">
        <summary>{t('proxyAdvanced')}</summary>
        <section className="dshDesktopSettingsGroup" aria-labelledby="dsh-desktop-proxy-title">
          <div>
            <h3 id="dsh-desktop-proxy-title">{t('proxyTitle')}</h3>
            <p className="dshDesktopSettingsGroupIntro">{t('proxyIntro')}</p>
          </div>
          <form className="dshDesktopSettingsPanel dshDesktopSettingsProxyForm" onSubmit={saveProxy}>
            <label className="dshDesktopSettingsField">
              <span>{t('proxyUrl')}</span>
              <input
                className="dshDesktopSettingsInput dshDesktopSettingsProxyInput"
                type="text"
                autoComplete="off"
                autoCapitalize="off"
                autoCorrect="off"
                spellCheck={false}
                value={proxyDraft}
                placeholder={t('proxyUrlPlaceholder')}
                disabled={!settingsWritable || busy !== undefined || restart !== 'none'}
                onChange={event => {
                  setProxyDraft(event.currentTarget.value)
                  setProxyInvalid(false)
                }}
              />
            </label>
            <div className="dshDesktopSettingsProxyActions">
              <button
                type="submit"
                className="dshDesktopSettingsPrimary"
                disabled={!settingsWritable || busy !== undefined || restart !== 'none'}
              >
                {t('proxySave')}
              </button>
            </div>
            {proxyInvalid && <p className="dshDesktopSettingsError" role="alert">{t('proxyInvalid')}</p>}
          </form>
        </section>
      </details>
    </div>
  )
}
