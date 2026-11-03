import { useCallback, useEffect, useState, type ReactNode } from 'react'
import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type { PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import type {} from '@deepseek-ai/dsh-client-ui-sidebar/client'
import { useAccountMenuPatched, useMenuAction } from './menu-actions.ts'
import { normalizeRelayOrigin } from './origin-input.ts'

type RemoteLocaleKey =
  | 'label' | 'waiting' | 'connected' | 'copy' | 'copied' | 'disabled' | 'close'
  | 'origin' | 'saveOrigin' | 'saving' | 'invalidOrigin' | 'saveFailed'

const NAMESPACE = 'overlay.remote'
const en: Record<RemoteLocaleKey, string> = {
  label: 'Mobile',
  waiting: 'Waiting for remote connection…',
  connected: 'Remote connected',
  copy: 'Copy pairing link',
  copied: 'Copied',
  disabled: 'Enter the relay origin below. It will be remembered for future launches.',
  close: 'Close',
  origin: 'Relay origin (https://…)',
  saveOrigin: 'Save and connect',
  saving: 'Saving…',
  invalidOrigin: 'Use an https:// relay origin. Public relays must use HTTPS; local development may use http://localhost.',
  saveFailed: 'Could not save the relay origin. Check the address and its TLS certificate.',
}
const zh: Record<RemoteLocaleKey, string> = {
  label: '手机连接',
  waiting: '等待远程连接…',
  connected: '远程已连接',
  copy: '复制配对链接',
  copied: '已复制',
  disabled: '在下方填写中继地址；保存后以后启动会自动记住。',
  close: '关闭',
  origin: '中继地址（https://…）',
  saveOrigin: '保存并连接',
  saving: '保存中…',
  invalidOrigin: '请使用 https:// 开头的中继地址。公网中继必须使用 HTTPS；本机开发可用 http://localhost。',
  saveFailed: '保存失败，请检查中继地址和 TLS 证书。',
}

declare module '@deepseek-ai/dsh-client-ui-slots' {
  interface LocaleNamespaceMap {
    'overlay.remote': RemoteLocaleKey
  }
}

type Translate = (key: RemoteLocaleKey) => string
type RemoteEntryProps = PropsRuntime<'sidebar.footer.action'> & { readonly t: Translate }

interface RemoteView {
  readonly state: string
  readonly waiting: boolean
  readonly connected: boolean
  readonly pairUrl: string | null
  readonly qrSvg: string | null
  readonly relayOrigin: string | null
  readonly error: string | null
}

const STATE_PATH = '/api/desktop/remote/state'
const OPEN_PATH = '/api/desktop/remote/pairing/open'
const STYLE_ID = 'dsh-overlay-remote-entry-styles'
const CSS = `
.dshOverlayRemoteEntry {
  box-sizing: border-box;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 32px;
  min-width: 32px;
  padding: 0;
  border: none;
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
  color: var(--dsw-alias-label-secondary);
  font: inherit;
  font-size: 13px;
  line-height: 20px;
}
.dshOverlayRemoteEntryWide {
  justify-content: flex-start;
  padding: 0 10px 0 8px;
  width: calc(100% + 4px);
  margin: 0 -2px;
  height: 42px;
}
.dshOverlayRemoteEntry:hover {
  background: var(--dsw-alias-interactive-bg-hover);
  color: var(--dsw-alias-label-primary);
}
.dshOverlayRemoteEntry:focus-visible {
  outline: none;
  box-shadow: 0 0 0 2px var(--dsw-alias-border-l3);
}
.dshOverlayRemoteOnline { color: var(--dsw-alias-state-success-primary); }
.dshOverlayRemoteOnline:hover { color: var(--dsw-alias-state-success-primary); }
.dshOverlayRemoteWaiting { color: var(--dsw-alias-state-warn-primary); }
.dshOverlayRemoteWaiting:hover { color: var(--dsw-alias-state-warn-primary); }
.dshOverlayRemoteEntryLabel {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.dshOverlayRemoteModal {
  position: fixed;
  z-index: 40;
  right: 24px;
  bottom: 24px;
  max-width: 360px;
  padding: 12px 14px;
  border: 0.5px solid var(--dsw-alias-settings-card-stroke);
  border-radius: var(--dsw-radius-xl);
  background: var(--dsw-alias-settings-card-fill);
  color: var(--dsw-alias-label-primary);
  font-size: 14px;
  line-height: 22px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.dshOverlayRemoteModal p { margin: 0; word-break: break-all; }
.dshOverlayRemoteError { color: var(--dsw-alias-state-error-primary, #e5484d); font-size: 13px; line-height: 20px; }
.dshOverlayRemoteQr {
  width: 232px;
  height: 232px;
  margin: 0 auto;
  color: var(--dsw-alias-label-primary);
}
.dshOverlayRemoteQr svg { width: 100%; height: 100%; display: block; }
`

function installRemoteStyles(): void {
  if (typeof document === 'undefined') return
  if (document.getElementById(STYLE_ID) !== null) return
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = CSS
  document.head.appendChild(style)
}

function PhoneIcon(): ReactNode {
  return <svg aria-hidden width="16" height="16" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
    <rect x="5" y="2" width="14" height="20" rx="2" ry="2" />
    <path d="M12 18h.01" />
  </svg>
}

export function OverlayRemoteEntry({ t, wide }: RemoteEntryProps): ReactNode {
  const [view, setView] = useState<RemoteView>()
  const [open, setOpen] = useState(false)
  const [copied, setCopied] = useState(false)
  const [originDraft, setOriginDraft] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string>()
  const menuPatched = useAccountMenuPatched()

  useEffect(() => {
    let stopped = false
    const read = (): void => {
      void fetch(STATE_PATH, { credentials: 'same-origin', headers: { accept: 'application/json' } })
        .then(async response => response.ok ? await response.json() as RemoteView : undefined)
        .then(next => { if (!stopped && next !== undefined) setView(next) })
        .catch(() => {})
    }
    read()
    const timer = window.setInterval(read, 4_000)
    return () => { stopped = true; window.clearInterval(timer) }
  }, [])

  const enable = useCallback(() => {
    void fetch(OPEN_PATH, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { accept: 'application/json' },
      redirect: 'error',
    }).then(async response => response.ok ? await response.json() as RemoteView : undefined)
      .then(next => { if (next !== undefined) setView(next) })
      .catch(() => {})
    setOpen(true)
  }, [])
  useMenuAction('remote', enable)

  const saveOrigin = useCallback(() => {
    const origin = normalizeRelayOrigin(originDraft)
    if (origin === null) {
      setError(t('invalidOrigin'))
      return
    }
    setError(undefined)
    setSaving(true)
    void fetch('/api/desktop/remote/origin', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { accept: 'application/json', 'content-type': 'application/json' },
      redirect: 'error',
      body: JSON.stringify({ origin }),
    }).then(async response => {
      const value = await response.json().catch(() => undefined) as RemoteView | undefined
      if (!response.ok || value === undefined) throw new Error('save failed')
      setView(value)
      setOriginDraft(value.relayOrigin ?? origin)
    }).catch(() => { setError(t('saveFailed')) })
      .finally(() => { setSaving(false) })
  }, [originDraft, t])

  const copy = useCallback(() => {
    if (view?.pairUrl == null) return
    void navigator.clipboard.writeText(view.pairUrl).then(() => {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    }).catch(() => {})
  }, [view?.pairUrl])

  const online = view?.connected === true
  const waiting = view?.waiting === true && !online
  return <>
    {menuPatched ? null : <button
      type="button"
      className={[
        'dshOverlayRemoteEntry',
        wide ? 'dshOverlayRemoteEntryWide' : '',
        online ? 'dshOverlayRemoteOnline' : waiting ? 'dshOverlayRemoteWaiting' : '',
      ].filter(part => part !== '').join(' ')}
      aria-label={t('label')}
      title={online ? t('connected') : waiting ? t('waiting') : t('label')}
      onClick={enable}
    >
      <PhoneIcon />
      {wide ? <span className="dshOverlayRemoteEntryLabel">{t('label')}</span> : null}
    </button>}
    {open ? <div className="dshOverlayRemoteModal" role="dialog" aria-modal="true" aria-label={t('label')}>
      {view?.state === 'disabled'
        ? <>
            <p>{t('disabled')}</p>
            <input className="dshOverlayInput" value={originDraft}
              placeholder={t('origin')} onChange={event => { setOriginDraft(event.target.value); setError(undefined) }} />
            <button type="button" className="dshOverlayBtnPrimary" onClick={saveOrigin} disabled={saving}>
              {saving ? t('saving') : t('saveOrigin')}
            </button>
            {error !== undefined ? <p className="dshOverlayRemoteError" role="alert">{error}</p> : null}
          </>
        : <>
            {view?.qrSvg != null && view.qrSvg !== ''
              ? <div className="dshOverlayRemoteQr" dangerouslySetInnerHTML={{ __html: view.qrSvg }} />
              : null}
            <p>{view?.pairUrl ?? view?.error ?? t('waiting')}</p>
          </>}
      {view?.pairUrl != null
        ? <button type="button" className="dshOverlayBtnSecondary" onClick={copy}>
            {copied ? t('copied') : t('copy')}
          </button>
        : null}
      <button type="button" className="dshOverlayBtnSecondary" onClick={() => setOpen(false)}>{t('close')}</button>
    </div> : null}
  </>
}

export function applyOverlayRemoteEntry(ctx: ClientContext): void {
  installRemoteStyles()
  ctx.effect(() => ctx.locale.register(NAMESPACE, { zh, en }), 'dsh-overlay-custom: remote locale')
  ctx.effect(() => ctx.slots.inject('sidebar.footer.action', () => ctx.slots.register({
    name: 'sidebar.footer.action',
    id: 'overlay-remote',
    priority: -1,
    order: 26,
    label: () => ctx.locale.bind(NAMESPACE)('label'),
    inject: () => ({ t: ctx.locale.bind(NAMESPACE) }) as never,
  }, OverlayRemoteEntry as never)), 'dsh-overlay-custom: remote sidebar entry')
}
