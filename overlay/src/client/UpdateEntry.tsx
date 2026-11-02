import { useCallback, useEffect, useState, type ReactNode } from 'react'
import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type { PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import type {} from '@deepseek-ai/dsh-client-ui-sidebar/client'

type UpdateLocaleKey =
  | 'label' | 'title' | 'current' | 'officialLatest' | 'buildLatest' | 'upToDate'
  | 'officialUpdate' | 'buildUpdate' | 'notes' | 'openOfficial' | 'openBuild'
  | 'refresh' | 'refreshing' | 'close' | 'unavailable' | 'dev'

const NAMESPACE = 'overlay.updates'
const en: Record<UpdateLocaleKey, string> = {
  label: 'Updates',
  title: 'Version and updates',
  current: 'Current',
  officialLatest: 'Official latest',
  buildLatest: 'This build latest',
  upToDate: 'Everything is up to date.',
  officialUpdate: 'The official app has a newer release. Rebuild or download it from the official releases page.',
  buildUpdate: 'A newer portable build is available for this repository.',
  notes: 'What changed',
  openOfficial: 'Open official releases',
  openBuild: 'Open this repository releases',
  refresh: 'Check now',
  refreshing: 'Checking…',
  close: 'Close',
  unavailable: 'Update check is unavailable right now.',
  dev: 'development build',
}
const zh: Record<UpdateLocaleKey, string> = {
  label: '更新',
  title: '版本与更新',
  current: '当前版本',
  officialLatest: '官方最新',
  buildLatest: '本仓库最新',
  upToDate: '已是最新。',
  officialUpdate: '官方已发布新版本，需要重新构建或到官方 Releases 下载官方包。',
  buildUpdate: '本仓库有新的便携包构建。',
  notes: '更新内容',
  openOfficial: '打开官方 Releases',
  openBuild: '打开本仓库 Releases',
  refresh: '立即检查',
  refreshing: '检查中…',
  close: '关闭',
  unavailable: '暂时无法检查更新。',
  dev: '开发构建',
}

declare module '@deepseek-ai/dsh-client-ui-slots' {
  interface LocaleNamespaceMap {
    'overlay.updates': UpdateLocaleKey
  }
}

type Translate = (key: UpdateLocaleKey) => string
type UpdateEntryProps = PropsRuntime<'sidebar.footer.action'> & { readonly t: Translate }

interface CandidateView {
  readonly version: string
  readonly url: string
  readonly name: string
  readonly notes: string
  readonly publishedAt: string | null
}

interface UpdatesView {
  readonly current: { readonly officialVersion: string; readonly buildVersion: string }
  readonly official: CandidateView | null
  readonly build: CandidateView | null
  readonly officialUpdate: boolean
  readonly buildUpdate: boolean
  readonly checkedAt: string | null
  readonly error: string | null
}

const STATE_PATH = '/api/desktop/updates/state'
const STYLE_ID = 'dsh-overlay-updates-entry-styles'
const CSS = `
.dshOverlayUpdateEntry {
  position: relative;
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
.dshOverlayUpdateEntry:hover { background: var(--dsw-alias-bg-hover); }
.dshOverlayUpdateEntryWide { justify-content: flex-start; padding: 0 10px 0 8px; width: calc(100% + 4px); margin: 0 -2px; height: 42px; }
.dshOverlayUpdateDot {
  position: absolute; top: 6px; right: 6px; width: 8px; height: 8px; border-radius: 50%;
  background: var(--dsw-alias-state-error-primary, #e5484d); box-shadow: 0 0 0 2px var(--dsw-alias-bg-base, #fff);
}
.dshOverlayUpdateModal {
  position: fixed; z-index: 80; top: 96px; left: 50%; transform: translateX(-50%);
  width: min(560px, calc(100vw - 32px)); max-height: calc(100vh - 160px); overflow: auto;
  box-sizing: border-box; padding: 16px; border-radius: 14px;
  background: var(--dsw-alias-bg-elevated, #fff); color: var(--dsw-alias-label-primary, #111);
  box-shadow: 0 18px 48px rgba(0, 0, 0, .24); font-size: 13px; line-height: 20px;
}
.dshOverlayUpdateModal h3 { margin: 0 0 10px; font-size: 15px; line-height: 22px; }
.dshOverlayUpdateRow { display: flex; gap: 8px; align-items: baseline; margin: 0 0 6px; }
.dshOverlayUpdateRow b { flex: 0 0 84px; font-weight: 600; opacity: .72; }
.dshOverlayUpdateNote { margin: 4px 0 10px; }
.dshOverlayUpdateAlert { margin: 0 0 10px; padding: 8px 10px; border-radius: 8px; background: var(--dsw-alias-bg-warning, #fff6e5); color: var(--dsw-alias-label-primary, #111); }
.dshOverlayUpdateNotes { margin: 0 0 12px; padding: 10px; max-height: 220px; overflow: auto; white-space: pre-wrap; border-radius: 8px; background: var(--dsw-alias-bg-base, #f7f7f8); }
.dshOverlayUpdateActions { display: flex; flex-wrap: wrap; gap: 8px; }
.dshOverlayUpdateActions a, .dshOverlayUpdateActions button {
  box-sizing: border-box; padding: 4px 10px; border-radius: 8px; border: 1px solid var(--dsw-alias-border-default, rgba(0,0,0,.12));
  background: transparent; color: inherit; font: inherit; text-decoration: none; cursor: pointer;
}
.dshOverlayUpdateActions a:hover, .dshOverlayUpdateActions button:hover { background: var(--dsw-alias-bg-hover, rgba(0,0,0,.05)); }
`

function BellIcon(): ReactNode {
  return <svg width="16" height="16" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
    <path fill="currentColor" d="M8 1.6a3.4 3.4 0 0 0-3.4 3.4v2.1l-1 1.8a.8.8 0 0 0 .7 1.2h7.4a.8.8 0 0 0 .7-1.2l-1-1.8V5A3.4 3.4 0 0 0 8 1.6Zm0 12.1a1.8 1.8 0 0 0 1.7-1.2H6.3A1.8 1.8 0 0 0 8 13.7Z" />
  </svg>
}

function installStyles(): void {
  if (typeof document === 'undefined' || document.getElementById(STYLE_ID) !== null) return
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = CSS
  document.head.append(style)
}

async function readState(): Promise<UpdatesView> {
  const response = await fetch(STATE_PATH, { credentials: 'same-origin', redirect: 'error', headers: { accept: 'application/json' } })
  const value = await response.json().catch(() => undefined) as (UpdatesView & { ok?: boolean }) | undefined
  if (!response.ok || value === undefined) throw new Error(`HTTP ${String(response.status)}`)
  return value
}

export function OverlayUpdateEntry({ t, wide }: UpdateEntryProps): ReactNode {
  const [view, setView] = useState<UpdatesView>()
  const [open, setOpen] = useState(false)
  const [checking, setChecking] = useState(false)
  const [failed, setFailed] = useState(false)

  const load = useCallback((force: boolean) => {
    setFailed(false)
    if (force) setChecking(true)
    void fetch(STATE_PATH, {
      method: force ? 'POST' : 'GET',
      credentials: 'same-origin',
      redirect: 'error',
      headers: { accept: 'application/json' },
    })
      .then(async response => {
        const value = await response.json().catch(() => undefined) as UpdatesView | undefined
        if (!response.ok || value === undefined) throw new Error(`HTTP ${String(response.status)}`)
        setView(value)
      })
      .catch(() => { setFailed(true) })
      .finally(() => { setChecking(false) })
  }, [])

  useEffect(() => { load(false) }, [load])
  useEffect(() => {
    const timer = window.setInterval(() => { load(false) }, 10 * 60 * 1000)
    return () => { window.clearInterval(timer) }
  }, [load])

  const pending = view !== undefined && (view.officialUpdate || view.buildUpdate)
  return <>
    <button
      type="button"
      className={['dshOverlayUpdateEntry', wide ? 'dshOverlayUpdateEntryWide' : ''].filter(part => part !== '').join(' ')}
      aria-label={t('label')}
      title={pending ? (view?.buildUpdate === true ? t('buildUpdate') : t('officialUpdate')) : t('label')}
      onClick={() => { setOpen(true); load(true) }}
    >
      <BellIcon />
      {wide ? <span>{t('label')}</span> : null}
      {pending ? <span className="dshOverlayUpdateDot" aria-hidden="true" /> : null}
    </button>
    {open ? <div className="dshOverlayUpdateModal" role="dialog" aria-modal="true" aria-label={t('title')}>
      <h3>{t('title')}</h3>
      <p className="dshOverlayUpdateRow"><b>{t('current')}</b><span>{view?.current.buildVersion ?? '…'}{view?.current.buildVersion.endsWith('_dev') === true ? ` · ${t('dev')}` : ''}</span></p>
      <p className="dshOverlayUpdateRow"><b>{t('officialLatest')}</b><span>{view?.official?.version ?? '—'}</span></p>
      <p className="dshOverlayUpdateRow"><b>{t('buildLatest')}</b><span>{view?.build?.version ?? '—'}</span></p>
      {failed || view?.error !== null && view?.error !== undefined ? <p className="dshOverlayUpdateAlert">{t('unavailable')}</p> : null}
      {pending ? <p className="dshOverlayUpdateAlert">{view?.buildUpdate === true ? t('buildUpdate') : t('officialUpdate')}</p> : null}
      {!pending && view !== undefined && view.error === null ? <p className="dshOverlayUpdateRow">{t('upToDate')}</p> : null}
      {view?.build?.notes !== undefined && view.build.notes !== '' ? <>
        <p className="dshOverlayUpdateNote"><b>{t('notes')}</b></p>
        <pre className="dshOverlayUpdateNotes">{view.build.notes}</pre>
      </> : null}
      {view?.official?.notes !== undefined && view.official.notes !== '' && view.build?.notes === undefined ? <>
        <p className="dshOverlayUpdateNote"><b>{t('notes')}</b></p>
        <pre className="dshOverlayUpdateNotes">{view.official.notes}</pre>
      </> : null}
      <div className="dshOverlayUpdateActions">
        <button type="button" onClick={() => { load(true) }} disabled={checking}>{checking ? t('refreshing') : t('refresh')}</button>
        {view?.official?.url !== undefined ? <a href={view.official.url} target="_blank" rel="noreferrer noopener">{t('openOfficial')}</a> : null}
        {view?.build?.url !== undefined ? <a href={view.build.url} target="_blank" rel="noreferrer noopener">{t('openBuild')}</a> : null}
        <button type="button" onClick={() => { setOpen(false) }}>{t('close')}</button>
      </div>
    </div> : null}
  </>
}

export function applyOverlayUpdateEntry(ctx: ClientContext): void {
  installStyles()
  ctx.effect(() => ctx.locale.register(NAMESPACE, { zh, en }), 'dsh-overlay-custom: updates locale')
  ctx.effect(() => ctx.slots.inject('sidebar.footer.action', () => ctx.slots.register({
    name: 'sidebar.footer.action',
    id: 'overlay-updates',
    priority: -1,
    order: 24,
    label: () => ctx.locale.bind(NAMESPACE)('label'),
    inject: () => ({ t: ctx.locale.bind(NAMESPACE) }) as never,
  }, OverlayUpdateEntry as never)), 'dsh-overlay-custom: update sidebar entry')
}
