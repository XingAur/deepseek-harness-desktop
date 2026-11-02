import { useCallback, useEffect, useState, type ReactNode } from 'react'
import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type { PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import type {} from '@deepseek-ai/dsh-client-ui-settings-models/client'
import { isOauthLoginProvider } from '../oauth-providers.ts'
import { injectOverlayStyles } from './overlay-styles.ts'
import { applyOverlayProxyFooter } from './ProxyFooter.tsx'

type ModelLocaleKey =
  | 'test' | 'testing' | 'testOk' | 'testFailed' | 'fetchList' | 'filled' | 'models'
  | 'login' | 'loggingIn' | 'openLogin' | 'cancelLogin' | 'loginOk' | 'loginUnavailable'
  | 'loginFailed' | 'submitCode'
  | 'statusOk' | 'statusBad' | 'statusIdle'
  | 'refreshLatest' | 'refreshing' | 'candidates' | 'saveSelection' | 'savedHint' | 'restartHint'
  | 'loginFirst' | 'proxyHint' | 'refreshNeedLogin' | 'refreshEmpty' | 'refreshApplied' | 'refreshReady'

const NAMESPACE = 'overlay.model'
const en: Record<ModelLocaleKey, string> = {
  test: 'Test connection',
  testing: 'Testing…',
  testOk: 'Connection OK',
  testFailed: 'Connection failed',
  fetchList: 'Fetch model list',
  filled: 'Live models',
  models: 'Models',
  login: 'Sign in (OAuth)',
  loggingIn: 'Waiting for browser…',
  openLogin: 'Open sign-in page',
  cancelLogin: 'Cancel sign-in',
  loginOk: 'Signed in',
  loginUnavailable: 'Official login API unavailable after this upgrade',
  loginFailed: 'Sign-in failed',
  submitCode: 'Submit',
  statusOk: 'Signed in',
  statusBad: 'Not signed in',
  statusIdle: 'Sign-in state unknown',
  refreshLatest: 'Refresh latest models',
  refreshing: 'Refreshing…',
  candidates: 'Models shown in picker',
  saveSelection: 'Save shown models',
  savedHint: 'Saved. Restart the app to apply.',
  restartHint: 'Checked models appear in the model picker after a restart.',
  loginFirst: 'Sign in first to fetch the newest models',
  proxyHint: 'OpenAI blocked this region. Check Model network below and the proxy exit region, then sign in again.',
  refreshNeedLogin: 'Sign in first, then refresh.',
  refreshEmpty: 'Live list was empty. Check the model proxy and try again.',
  refreshApplied: 'Wrote the live list. Restart to update the picker.',
  refreshReady: 'Updated the list below. Save, then restart to update the picker.',
}
const zh: Record<ModelLocaleKey, string> = {
  test: '测试连接',
  testing: '测试中…',
  testOk: '连接正常',
  testFailed: '连接失败',
  fetchList: '拉取模型列表',
  filled: '线上模型',
  models: '模型',
  login: '登录（OAuth）',
  loggingIn: '等待浏览器…',
  openLogin: '打开登录页',
  cancelLogin: '取消登录',
  loginOk: '已登录',
  loginUnavailable: '本次官网升级后登录接口不可用',
  loginFailed: '登录失败',
  submitCode: '提交',
  statusOk: '已登录',
  statusBad: '未登录',
  statusIdle: '登录状态未知',
  refreshLatest: '刷新最新模型',
  refreshing: '刷新中…',
  candidates: '显示在模型选择器中的模型',
  saveSelection: '保存显示的模型',
  savedHint: '已保存，重启应用后生效。',
  restartHint: '勾选的模型将在重启后出现在模型选择器。',
  loginFirst: '请先登录，才能拉取最新模型',
  proxyHint: 'OpenAI 拒绝了当前地区。请检查下方「模型网络」的连接模式、检测结果和代理出口地区，再登录。',
  refreshNeedLogin: '请先登录，再刷新。',
  refreshEmpty: '线上列表是空的。请检查「模型网络」中的生效路由后再试。',
  refreshApplied: '已写入最新列表。重启后选择器才会更新。',
  refreshReady: '已更新下方列表。勾选后点保存，再重启选择器才会变。',
}

declare module '@deepseek-ai/dsh-client-ui-slots' {
  interface LocaleNamespaceMap {
    'overlay.model': ModelLocaleKey
  }
}

type Translate = (key: ModelLocaleKey) => string
interface ExtrasProps extends PropsRuntime<'settings.models.provider-card'> {
  readonly t: Translate
}

interface ProbeView {
  readonly ok: boolean
  readonly status: number
  readonly models: readonly string[]
  readonly error: string | null
}

interface LiveView {
  readonly ok: boolean
  readonly live: readonly string[]
  readonly configured: readonly string[]
  readonly error?: string
  readonly applied?: boolean
}

const PROBE_PATH = '/api/desktop/models/probe'
const LIVE_PATH = '/api/desktop/models/live'
const SELECTION_PATH = '/api/desktop/models/selection'
const OAUTH_BEGIN = '/api/desktop/oauth/begin'
const OAUTH_STATUS = '/api/desktop/oauth/status'
const OAUTH_CANCEL = '/api/desktop/oauth/cancel'
const OAUTH_ANSWER = '/api/desktop/oauth/answer'

interface OauthView {
  readonly phase: 'idle' | 'running' | 'need-prompt' | 'authorized' | 'cancelled' | 'failed' | 'unavailable'
  readonly message: string | null
  readonly url: string | null
  readonly code: string | null
  readonly prompt: {
    readonly message: string
    readonly placeholder: string | null
    readonly options?: readonly { readonly id: string; readonly label: string }[]
  } | null
  readonly error: string | null
}

function statusDotClass(phase: OauthView['phase']): string {
  if (phase === 'authorized') return 'dshOverlayDotOk'
  if (phase === 'failed' || phase === 'cancelled') return 'dshOverlayDotBad'
  if (phase === 'running' || phase === 'need-prompt') return 'dshOverlayDotBusy'
  return 'dshOverlayDotIdle'
}

async function postJson<T>(path: string, body: unknown): Promise<T | undefined> {
  const response = await fetch(path, {
    method: 'POST',
    credentials: 'same-origin',
    redirect: 'error',
    headers: { accept: 'application/json', 'content-type': 'application/json' },
    body: JSON.stringify(body),
  })
  return await response.json().catch(() => undefined) as T | undefined
}

export function OverlayProviderCardExtras(props: ExtrasProps): ReactNode {
  const { t } = props
  const provider = props.provider.provider
  const oauth = isOauthLoginProvider(provider)
  const [probe, setProbe] = useState<ProbeView>()
  const [busy, setBusy] = useState<'probe' | 'fetch' | 'refresh' | 'save'>()
  const [oauthView, setOauthView] = useState<OauthView>()
  const [code, setCode] = useState('')
  const [candidates, setCandidates] = useState<string[]>([])
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [saved, setSaved] = useState(false)
  const [refreshNote, setRefreshNote] = useState<string>()

  const run = useCallback(async (mode: 'probe' | 'fetch'): Promise<void> => {
    if (busy !== undefined) return
    setBusy(mode)
    setProbe(undefined)
    try {
      const response = await fetch(PROBE_PATH, {
        method: 'POST',
        credentials: 'same-origin',
        redirect: 'error',
        headers: { accept: 'application/json', 'content-type': 'application/json' },
        body: JSON.stringify({ settingsNs: props.provider.settingsNs, provider }),
      })
      const view = await response.json().catch(() => undefined) as ProbeView | undefined
      if (view !== undefined && typeof view.ok === 'boolean') {
        setProbe(view)
        return
      }
      throw new Error(`HTTP ${String(response.status)}`)
    } catch (cause) {
      setProbe({ ok: false, status: 0, models: [], error: cause instanceof Error ? cause.message : String(cause) })
    } finally {
      setBusy(undefined)
    }
  }, [busy, props.provider.settingsNs, provider])

  const applyLive = useCallback((view: LiveView): void => {
    const ids = view.applied === true && view.configured.length > 0
      ? [...view.configured]
      : [...new Set([...view.live, ...view.configured])].sort()
    setCandidates(ids)
    setChecked(new Set(ids))
  }, [])

  const refreshLatest = useCallback((): void => {
    if (busy !== undefined) return
    setBusy('refresh')
    setSaved(false)
    setRefreshNote(undefined)
    void postJson<LiveView>(LIVE_PATH, { provider }).then(view => {
      if (view === undefined) {
        setRefreshNote(t('refreshEmpty'))
        return
      }
      if (!view.ok) {
        setRefreshNote(view.error === 'no-credential' ? t('refreshNeedLogin')
          : view.error === 'proxy-required' ? t('proxyHint')
            : `${t('refreshEmpty')}${view.error !== undefined ? ` (${view.error})` : ''}`)
        if (view.configured.length > 0) applyLive(view)
        return
      }
      applyLive(view)
      setRefreshNote(t('refreshReady'))
    }).finally(() => { setBusy(undefined) })
  }, [applyLive, busy, provider, t])

  const toggle = useCallback((id: string): void => {
    setChecked(previous => {
      const next = new Set(previous)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
    setSaved(false)
  }, [])

  const saveSelection = useCallback((): void => {
    if (busy !== undefined) return
    setBusy('save')
    void postJson<{ ok: boolean }>(SELECTION_PATH, { provider, models: [...checked] })
      .then(outcome => { if (outcome?.ok === true) setSaved(true) })
      .finally(() => { setBusy(undefined) })
  }, [busy, checked, provider])

  useEffect(() => {
    if (!oauth) return
    let cancelled = false
    const tick = async (): Promise<void> => {
      try {
        const response = await fetch(`${OAUTH_STATUS}?provider=${encodeURIComponent(provider)}`, {
          credentials: 'same-origin',
          redirect: 'error',
          headers: { accept: 'application/json' },
        })
        const payload = await response.json().catch(() => undefined) as OauthView | undefined
        if (cancelled || payload === undefined) return
        setOauthView(payload)
      } catch {
        // Polling must not throw into the official settings page.
      }
    }
    void tick()
    void fetch(`${SELECTION_PATH}?provider=${encodeURIComponent(provider)}`, {
      credentials: 'same-origin',
      redirect: 'error',
      headers: { accept: 'application/json' },
    }).then(async response => {
      const payload = await response.json().catch(() => undefined) as { ok?: boolean; models?: string[] } | undefined
      if (cancelled || payload?.ok !== true || !Array.isArray(payload.models)) return
      setCandidates(previous => [...new Set([...previous, ...payload.models!])].sort())
      setChecked(previous => previous.size > 0 ? previous : new Set(payload.models!))
    }).catch(() => {})
    const id = window.setInterval(() => { void tick() }, 1500)
    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [oauth, provider])

  useEffect(() => {
    const url = oauthView?.url
    if (typeof url !== 'string' || url === '') return
    window.open(url, '_blank', 'noopener')
  }, [oauthView?.url])

  const startLogin = useCallback(() => {
    void postJson<OauthView>(OAUTH_BEGIN, { provider }).then(view => {
      if (view !== undefined) setOauthView(view)
      else setOauthView({
        phase: 'failed',
        message: null,
        url: null,
        code: null,
        prompt: null,
        error: 'login-request-failed',
      })
    })
  }, [provider])

  const cancelLogin = useCallback(() => {
    void postJson<OauthView>(OAUTH_CANCEL, { provider }).then(view => {
      if (view !== undefined) setOauthView(view)
    })
  }, [provider])

  const submitCode = useCallback(() => {
    void postJson<OauthView>(OAUTH_ANSWER, { provider, text: code }).then(view => {
      if (view !== undefined) setOauthView(view)
      setCode('')
    })
  }, [code, provider])

  const phase = oauthView?.phase ?? 'idle'
  const statusText = phase === 'authorized' ? t('statusOk') : phase === 'failed' || phase === 'cancelled'
    ? t('statusBad') : t('statusIdle')

  return <div className="dshOverlayProviderExtras">
    {oauth
      ? <div>
          <div className="dshOverlayRow">
            <span role="img" aria-label={statusText} title={statusText}
              className={`dshOverlayDot ${statusDotClass(phase)}`} />
            <button type="button" className="dshOverlayBtnPrimary" onClick={startLogin}
              disabled={phase === 'running' || phase === 'need-prompt'}>
              {phase === 'running' || phase === 'need-prompt'
                ? t('loggingIn')
                : phase === 'authorized' ? t('loginOk') : t('login')}
            </button>
            {oauthView?.url !== null && oauthView?.url !== undefined
              ? <a className="dshOverlayLink" href={oauthView.url} target="_blank" rel="noreferrer">{t('openLogin')}</a>
              : null}
            {oauthView?.code !== null && oauthView?.code !== undefined ? <code>{oauthView.code}</code> : null}
            {phase === 'running' || phase === 'need-prompt'
              ? <button type="button" className="dshOverlayBtnSecondary" onClick={cancelLogin}>{t('cancelLogin')}</button>
              : null}
          </div>
          {oauthView?.phase === 'need-prompt'
            ? <div>
                <p className="dshOverlayHint">{oauthView.prompt?.message}</p>
                {(oauthView.prompt?.options ?? []).length > 0
                  ? <div className="dshOverlayRow">
                      {oauthView.prompt?.options?.map(option => (
                        <button key={option.id} type="button" className="dshOverlayBtnSecondary"
                          onClick={() => {
                            void postJson<OauthView>(OAUTH_ANSWER, { provider, text: option.id }).then(view => {
                              if (view !== undefined) setOauthView(view)
                            })
                          }}>{option.label}</button>
                      ))}
                    </div>
                  : <label className="dshOverlayCheck">
                      <input className="dshOverlayInput" value={code} onChange={event => { setCode(event.target.value) }}
                        placeholder={oauthView.prompt?.placeholder ?? ''} />
                      <button type="button" className="dshOverlayBtnSecondary" onClick={submitCode}>{t('submitCode')}</button>
                    </label>}
              </div>
            : null}
          {phase === 'unavailable' ? <p className="dshOverlayHint" role="status">{t('loginUnavailable')}</p> : null}
          {phase === 'failed'
            ? <p className="dshOverlayAlert" role="alert">{t('loginFailed')}: {
                oauthView?.error === 'proxy-required'
                  || (oauthView?.error ?? '').includes('unsupported_country')
                  ? t('proxyHint')
                  : (oauthView?.error ?? '')
              }</p>
            : null}
          <div className="dshOverlayRow">
            <button type="button" className="dshOverlayBtnSecondary" disabled={busy !== undefined} onClick={refreshLatest}>
              {busy === 'refresh' ? t('refreshing') : t('refreshLatest')}
            </button>
            {phase !== 'authorized' && candidates.length === 0
              ? <span className="dshOverlayHint">{t('loginFirst')}</span> : null}
            {refreshNote !== undefined
              ? <p className={saved ? 'dshOverlaySaved' : 'dshOverlayAlert'} role="status">{refreshNote}</p>
              : null}
          </div>
          {candidates.length > 0
            ? <details className="dshOverlayFieldset">
                <summary>{t('candidates')}</summary>
                {candidates.map(id => (
                  <label className="dshOverlayCheck" key={id}>
                    <input type="checkbox" checked={checked.has(id)} onChange={() => { toggle(id) }} />
                    {id}
                  </label>
                ))}
                <button type="button" className="dshOverlayBtnPrimary" disabled={busy !== undefined} onClick={saveSelection}>
                  {t('saveSelection')}
                </button>
                {saved ? <p className="dshOverlaySaved" role="status">{t('savedHint')}</p> : null}
                <p className="dshOverlayHint">{t('restartHint')}</p>
              </details>
            : null}
        </div>
      : null}
    {!oauth && provider === 'deepseek'
      ? <>
          <div className="dshOverlayRow">
            <button type="button" className="dshOverlayBtnSecondary" disabled={busy !== undefined}
              onClick={() => { void run('probe') }}>
              {busy === 'probe' ? t('testing') : probe?.ok === true ? t('testOk') : t('test')}
            </button>
            <button type="button" className="dshOverlayBtnSecondary" disabled={busy !== undefined}
              onClick={() => { void run('fetch') }}>
              {t('fetchList')}
            </button>
          </div>
          {probe !== undefined && !probe.ok
            ? <p className="dshOverlayAlert" role="alert">{t('testFailed')}: {probe.error ?? ''}</p>
            : null}
          {probe?.ok === true && probe.models.length > 0
            ? <ul aria-label={t('models')}>
                {probe.models.map(id => <li key={id}>{id}</li>)}
              </ul>
            : null}
        </>
      : null}
  </div>
}

export function applyOverlayModelExtras(ctx: ClientContext): void {
  injectOverlayStyles()
  applyOverlayProxyFooter(ctx)
  ctx.effect(() => ctx.locale.register(NAMESPACE, { zh, en }), 'dsh-overlay-custom: model locale')
  const extras = () => ({ t: ctx.locale.bind(NAMESPACE) }) as never
  ctx.effect(() => ctx.slots.inject('settings.models.provider-card', () => ctx.slots.register({
    name: 'settings.models.provider-card',
    key: 'llm-deepseek',
    inject: extras,
  }, OverlayProviderCardExtras as never)), 'dsh-overlay-custom: deepseek extras')
  ctx.effect(() => ctx.slots.inject('settings.models.provider-card', () => ctx.slots.register({
    name: 'settings.models.provider-card',
    key: 'llm-pi-ai',
    inject: extras,
  }, OverlayProviderCardExtras as never)), 'dsh-overlay-custom: pi-ai extras')
}
