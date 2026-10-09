import { useCallback, useEffect, useState, type ReactNode } from 'react'
import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type { PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import type {} from '@deepseek-ai/dsh-client-ui-settings-models/client'

type ProxyLocaleKey =
  | 'title' | 'intro' | 'url' | 'xai' | 'codex' | 'save' | 'test' | 'testing'
  | 'saved' | 'testOk' | 'testFail'

const NAMESPACE = 'overlay.proxy'
const en: Record<ProxyLocaleKey, string> = {
  title: 'Model proxy',
  intro: 'Only checked overseas providers use this proxy. DeepSeek and Zhipu stay direct.',
  url: 'Proxy URL',
  xai: 'xAI / Grok',
  codex: 'OpenAI / Codex',
  save: 'Save',
  test: 'Test',
  testing: 'Testing…',
  saved: 'Saved. New requests use this proxy.',
  testOk: 'Reached api.x.ai through the proxy.',
  testFail: 'Proxy test failed',
}
const zh: Record<ProxyLocaleKey, string> = {
  title: '模型代理',
  intro: '只有勾选的海外厂商走这条代理。DeepSeek 和智谱保持直连。',
  url: '代理 URL',
  xai: 'xAI / Grok',
  codex: 'OpenAI / Codex',
  save: '保存',
  test: '测试',
  testing: '测试中…',
  saved: '已保存，后续请求会走这条代理。',
  testOk: '已通过代理到达 api.x.ai。',
  testFail: '代理测试失败',
}

declare module '@deepseek-ai/dsh-client-ui-slots' {
  interface LocaleNamespaceMap {
    'overlay.proxy': ProxyLocaleKey
  }
}

type Translate = (key: ProxyLocaleKey) => string
interface FooterProps extends PropsRuntime<'settings.models.footer'> {
  readonly t: Translate
}

interface ProxyConfigView {
  readonly ok?: boolean
  readonly url?: string
  readonly origin?: string
  readonly providers?: readonly string[]
  readonly error?: string
}

export function OverlayProxyFooter(props: FooterProps): ReactNode {
  const { t } = props
  const [url, setUrl] = useState('')
  const [providers, setProviders] = useState<readonly string[]>(['xai', 'openai-codex'])
  const [busy, setBusy] = useState<'save' | 'test'>()
  const [saved, setSaved] = useState(false)
  const [test, setTest] = useState<{ ok: boolean; error: string | null }>()

  useEffect(() => {
    void fetch('/api/desktop/proxy/config', {
      credentials: 'same-origin',
      redirect: 'error',
      headers: { accept: 'application/json' },
    }).then(async response => {
      const payload = await response.json() as ProxyConfigView
      if (payload.ok === true) {
        setUrl(payload.url ?? '')
        if (Array.isArray(payload.providers) && payload.providers.length > 0) {
          setProviders(payload.providers)
        }
      }
    }).catch(() => {})
  }, [])

  const toggle = (id: string): void => {
    setProviders(current => current.includes(id) ? current.filter(item => item !== id) : [...current, id])
    setSaved(false)
  }

  const save = useCallback(() => {
    setBusy('save')
    setSaved(false)
    void fetch('/api/desktop/proxy/config', {
      method: 'POST',
      credentials: 'same-origin',
      redirect: 'error',
      headers: { accept: 'application/json', 'content-type': 'application/json' },
      body: JSON.stringify({ url, providers }),
    }).then(async response => {
      const payload = await response.json() as ProxyConfigView
      if (payload.ok === true) setSaved(true)
    }).finally(() => { setBusy(undefined) })
  }, [providers, url])

  const runTest = useCallback(() => {
    setBusy('test')
    setTest(undefined)
    void fetch('/api/desktop/proxy/test', {
      method: 'POST',
      credentials: 'same-origin',
      redirect: 'error',
      headers: { accept: 'application/json', 'content-type': 'application/json' },
      body: JSON.stringify({ url }),
    }).then(async response => {
      const payload = await response.json() as { ok?: boolean; error?: string | null }
      const result = { ok: payload.ok === true, error: payload.error ?? null }
      setTest(result)
      if (result.ok) {
        const savedResponse = await fetch('/api/desktop/proxy/config', {
          method: 'POST',
          credentials: 'same-origin',
          redirect: 'error',
          headers: { accept: 'application/json', 'content-type': 'application/json' },
          body: JSON.stringify({ url, providers }),
        })
        const savedPayload = await savedResponse.json() as ProxyConfigView
        if (savedPayload.ok === true) setSaved(true)
      }
    }).finally(() => { setBusy(undefined) })
  }, [providers, url])

  return <section className="dshOverlayProxyFooter" aria-labelledby="dsh-overlay-proxy-title">
    <h3 className="dshOverlayTitle" id="dsh-overlay-proxy-title">{t('title')}</h3>
    <p className="dshOverlayIntro">{t('intro')}</p>
    <label className="dshOverlayCheck">{t('url')}
      <input className="dshOverlayInput" value={url} type="url"
        onChange={event => { setUrl(event.target.value); setSaved(false) }} />
    </label>
    <div className="dshOverlayRow">
      <label className="dshOverlayCheck">
        <input type="checkbox" checked={providers.includes('xai')} onChange={() => { toggle('xai') }} />
        {t('xai')}
      </label>
      <label className="dshOverlayCheck">
        <input type="checkbox" checked={providers.includes('openai-codex')}
          onChange={() => { toggle('openai-codex') }} />
        {t('codex')}
      </label>
    </div>
    <div className="dshOverlayRow">
      <button type="button" className="dshOverlayBtnSecondary" disabled={busy !== undefined} onClick={runTest}>
        {busy === 'test' ? t('testing') : t('test')}
      </button>
      <button type="button" className="dshOverlayBtnPrimary" disabled={busy !== undefined} onClick={save}>
        {t('save')}
      </button>
    </div>
    {saved ? <p className="dshOverlaySaved" role="status">{t('saved')}</p> : null}
    {test?.ok === true ? <p className="dshOverlaySaved" role="status">{t('testOk')}</p> : null}
    {test?.ok === false
      ? <p className="dshOverlayAlert" role="alert">{t('testFail')}{test.error !== null ? `: ${test.error}` : ''}</p>
      : null}
  </section>
}

export function applyOverlayProxyFooter(ctx: ClientContext): void {
  ctx.effect(() => ctx.locale.register(NAMESPACE, { zh, en }), 'dsh-overlay-custom: proxy locale')
  ctx.effect(() => ctx.slots.inject('settings.models.footer', () => ctx.slots.register({
    name: 'settings.models.footer',
    id: 'overlay-model-proxy',
    inject: () => ({ t: ctx.locale.bind(NAMESPACE) }) as never,
  }, OverlayProxyFooter as never)), 'dsh-overlay-custom: proxy footer')
}
