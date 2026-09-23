/** Settings sidebar page for per-provider model account libraries. */

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import type { InjectFace, PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import type { DesktopAccountsListView, DesktopModelSyncView, DesktopSettingsApi } from './desktop-settings-api.ts'

/** Registration-side business face for the model-account settings section. */
export interface DesktopAccountsSectionInjected {
  readonly api: DesktopSettingsApi
}

/** Renderer-composed props for the model-account settings section. */
export type DesktopAccountsSectionProps =
  PropsRuntime<'settings.section'>
  & PropsLocale<'desktop.accounts'>
  & InjectFace<DesktopAccountsSectionInjected>

type Status = 'idle' | 'switched' | 'loggedOut' | 'removed' | 'addStarted'
const POLL_INTERVAL_MS = 2_000

/** Render the Desktop model-account settings page. */
export function DesktopAccountsSection({ t, api }: DesktopAccountsSectionProps): ReactNode {
  const [view, setView] = useState<DesktopAccountsListView>()
  const [busy, setBusy] = useState(false)
  const [failed, setFailed] = useState(false)
  const [status, setStatus] = useState<Status>('idle')
  const [statusDetail, setStatusDetail] = useState('')
  const [syncing, setSyncing] = useState(false)
  const [syncResult, setSyncResult] = useState<DesktopModelSyncView>()
  const [syncFailed, setSyncFailed] = useState(false)
  const alive = useRef(true)

  const refresh = useCallback(async (): Promise<void> => {
    try {
      if (typeof api.listAccounts !== 'function') {
        if (alive.current) setFailed(true)
        return
      }
      const value = await api.listAccounts()
      if (alive.current) {
        setView(value)
        setFailed(false)
      }
    } catch {
      if (alive.current) setFailed(true)
    }
  }, [api])

  useEffect(() => {
    alive.current = true
    void refresh()
    const timer = setInterval(() => { void refresh() }, POLL_INTERVAL_MS)
    return () => {
      alive.current = false
      clearInterval(timer)
    }
  }, [refresh])

  const run = useCallback(async (operation: () => Promise<void>): Promise<void> => {
    setBusy(true)
    setFailed(false)
    try {
      await operation()
    } catch {
      setFailed(true)
    } finally {
      setBusy(false)
    }
  }, [])

  const mutate = useCallback(async (
    operation: () => Promise<{ readonly restartRecommended: boolean }>,
    done: Status,
  ): Promise<void> => {
    await run(async () => {
      const outcome = await operation()
      if (!outcome.restartRecommended) setStatusDetail('')
      await refresh()
      setStatus(done)
    })
  }, [refresh, run])

  const add = (provider: string): void => {
    void mutate(async () => await api.addModelAccount(provider), 'addStarted')
  }
  const syncModels = (): void => {
    setSyncing(true)
    setSyncFailed(false)
    api.syncModels()
      .then(result => { if (alive.current) setSyncResult(result) })
      .catch(() => { if (alive.current) setSyncFailed(true) })
      .finally(() => { if (alive.current) setSyncing(false) })
  }
  const switchTo = (provider: string, account: string): void => {
    void mutate(async () => await api.switchModelAccount(provider, account), 'switched')
  }
  const remove = (provider: string, account: string): void => {
    void mutate(async () => await api.removeModelAccount(provider, account), 'removed')
  }
  const logout = (provider: string): void => {
    void mutate(async () => await api.logoutModelAccount(provider), 'loggedOut')
  }

  if (view === undefined) {
    return (
      <div className="dshDesktopSettings">
        <header className="dshDesktopSettingsHeader">
          <h2>{t('title')}</h2>
          <p>{t('intro')}</p>
        </header>
        {failed
          ? (
              <p className="dshDesktopSettingsError" role="alert">
                {t('operationFailed')}
                {' '}
                <button type="button" className="dshDesktopSettingsButton" onClick={() => { void refresh() }}>
                  {t('retry')}
                </button>
              </p>
            )
          : <p className="dshDesktopSettingsHint">{t('loading')}</p>}
      </div>
    )
  }

  return (
    <div className="dshDesktopSettings">
      <header className="dshDesktopSettingsHeader">
        <h2>{t('title')}</h2>
        <p>{t('intro')}</p>
      </header>

      {!view.available && <p className="dshDesktopSettingsHint">{t('unavailable')}</p>}
      {view.available && view.providers.length === 0 && (
        <p className="dshDesktopSettingsHint">{t('empty')}</p>
      )}
      {failed && <p className="dshDesktopSettingsError" role="alert">{t('operationFailed')}</p>}
      {status !== 'idle' && (
        <p className="dshDesktopSettingsSuccess" role="status">
          {status === 'switched' ? t('switchedRestart')
            : status === 'loggedOut' ? t('loggedOutRestart')
            : status === 'removed' ? (statusDetail === '' ? t('removedDone') : `${t('removedDone')} (${statusDetail})`)
            : t('addStarted')}
        </p>
      )}

      {view.available && (
        <section className="dshDesktopSettingsCard" aria-labelledby="dsh-desktop-model-sync-title">
          <div className="dshDesktopSettingsCardHead">
            <h3 id="dsh-desktop-model-sync-title">{t('syncTitle')}</h3>
            <span className="dshDesktopSettingsCardHeadActions">
              <button
                type="button"
                className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
                disabled={syncing}
                onClick={syncModels}
              >
                {syncing ? t('syncing') : t('syncAction')}
              </button>
            </span>
          </div>
          <p className="dshDesktopSettingsHint">{t('syncIntro')}</p>
          {syncFailed && (
            <p className="dshDesktopSettingsError" role="alert">{t('syncFailed')}</p>
          )}
          {syncResult !== undefined && syncResult.results.map(result => (
            <div key={result.provider} className="dshDesktopSettingsCheckRow">
              <span>
                <strong>{result.provider}</strong>
                {' — '}
                {result.status === 'ok'
                  ? (result.discovered.length > 0 ? `${t('syncFoundPrefix')}${result.discovered.join(', ')}` : t('syncUpToDate'))
                  : `${t('syncSkipped')}${result.detail === undefined ? '' : ` (${result.detail})`}`}
              </span>
            </div>
          ))}
          {syncResult !== undefined && (
            <p className="dshDesktopSettingsHint">{t('syncUsageHint')}</p>
          )}
        </section>
      )}
      {view.providers.map(provider => (
        <section
          key={provider.provider}
          className="dshDesktopSettingsGroup"
          aria-labelledby={`dsh-desktop-accounts-${provider.provider}`}
        >
          <div>
            <h3 id={`dsh-desktop-accounts-${provider.provider}`}>{provider.label}</h3>
          </div>
          <div className="dshDesktopSettingsPanel">
            <div className="dshDesktopSettingsList">
              <div className="dshDesktopSettingsCheckRow">
                <span>
                  <strong>{t('activePrefix')}</strong>
                  {provider.activeLabel === null ? t('notSignedIn') : provider.activeLabel}
                  {provider.inFlight ? ` — ${t('inFlight')}` : ''}
                </span>
                <button
                  type="button"
                  className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
                  disabled={busy || provider.activeLabel === null}
                  onClick={() => { logout(provider.provider) }}
                >
                  {t('logoutAccount')}
                </button>
              </div>
              {provider.saved.length > 0 && <p className="dshDesktopSettingsHint">{t('savedPrefix')}</p>}
              {provider.saved.map(account => (
                <div key={account.id} className="dshDesktopSettingsCheckRow">
                  <span>{account.label}</span>
                  <span className="dshDesktopSettingsProxyActions">
                    <button
                      type="button"
                      className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
                      disabled={busy}
                      onClick={() => { switchTo(provider.provider, account.id) }}
                    >
                      {t('activateAccount')}
                    </button>
                    <button
                      type="button"
                      className="dshDesktopSettingsButton dshDesktopSettingsButtonSecondary"
                      disabled={busy}
                      onClick={() => { remove(provider.provider, account.id) }}
                    >
                      {t('removeAccount')}
                    </button>
                  </span>
                </div>
              ))}
            </div>
            <div className="dshDesktopSettingsProxyActions">
              <button
                type="button"
                className="dshDesktopSettingsPrimary"
                disabled={busy || provider.inFlight}
                onClick={() => { add(provider.provider) }}
              >
                {provider.inFlight ? t('adding') : t('addAccount')}
              </button>
            </div>
          </div>
        </section>
      ))}
    </div>
  )
}
