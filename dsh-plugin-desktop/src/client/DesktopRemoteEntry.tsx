/**
 * Sidebar remote-control entry: replaces the upstream Agents-Anywhere footer
 * action (same slot id, lower priority — the list projection keeps the first
 * ledger entry per id) with the fork's relay pairing flow.
 */

import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Smartphone } from 'lucide-react'
import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type { PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import type {} from '@deepseek-ai/dsh-client-ui-sidebar/client'
import {
  DESKTOP_REMOTE_LOCALE_NAMESPACE,
  en,
  zh,
  type DesktopRemoteLocaleKey,
} from './desktop-remote-locales.ts'

declare module '@deepseek-ai/dsh-client-ui-slots' {
  interface LocaleNamespaceMap {
    /** Sidebar remote-control entry copy. */
    'desktop.remote': DesktopRemoteLocaleKey
  }
}

/** Translator bound by the registration to the remote-entry locale namespace. */
export type DesktopRemoteTranslate = (key: DesktopRemoteLocaleKey) => string

export type DesktopRemoteEntryProps = PropsRuntime<'sidebar.footer.action'> & {
  readonly t: DesktopRemoteTranslate
}

interface RemoteStateView {
  readonly state: 'disabled' | 'off' | 'connecting' | 'ready' | 'failed'
  readonly waiting: boolean
  readonly connected: boolean
  readonly relayOrigin: string | null
}

const STATE_PATH = '/api/desktop/remote/state'
const OPEN_PATH = '/api/desktop/remote/pairing/open'
const POLL_MS = 4_000

const STYLE_ID = 'dsh-desktop-remote-entry-styles'
const CSS = `
.dshDesktopRemoteEntry {
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
.dshDesktopRemoteEntryWide { justify-content: flex-start; padding: 0 10px 0 8px; width: calc(100% + 4px); margin: 0 -2px; height: 42px; }
.dshDesktopRemoteEntry:hover { background: var(--dsw-alias-interactive-bg-hover); color: var(--dsw-alias-label-primary); }
.dshDesktopRemoteEntry:focus-visible { outline: none; box-shadow: 0 0 0 2px var(--dsw-alias-border-l3); }
.dshDesktopRemoteEntryOnline { color: var(--dsw-alias-state-success-primary); }
.dshDesktopRemoteEntryOnline:hover { color: var(--dsw-alias-state-success-primary); }
.dshDesktopRemoteEntryWaiting { color: var(--dsw-alias-state-warn-primary); }
.dshDesktopRemoteEntryWaiting:hover { color: var(--dsw-alias-state-warn-primary); }
.dshDesktopRemoteEntryLabel { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
`

function installDesktopRemoteEntryStyles(): void {
  if (typeof document === 'undefined') return
  if (document.getElementById(STYLE_ID) !== null) return
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = CSS
  document.head.appendChild(style)
}

/**
 * The footer action the sidebar renders for remote control: a phone glyph that
 * turns green while the relay tunnel is up, opening the pairing window on click.
 */
export function DesktopRemoteEntry({ t, wide }: DesktopRemoteEntryProps): ReactNode {
  const [state, setState] = useState<RemoteStateView | undefined>()

  useEffect(() => {
    let stopped = false
    const read = (): void => {
      void fetch(STATE_PATH, { credentials: 'same-origin', headers: { accept: 'application/json' } })
        .then(async response => {
          if (!response.ok) return undefined
          return await response.json() as RemoteStateView
        })
        .then(view => {
          if (!stopped && view !== undefined) setState(view)
        })
        .catch(() => {})
    }
    read()
    const timer = window.setInterval(read, POLL_MS)
    return () => {
      stopped = true
      window.clearInterval(timer)
    }
  }, [])

  const open = useCallback(() => {
    void fetch(OPEN_PATH, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { accept: 'application/json' },
      redirect: 'error',
    }).catch(() => {})
  }, [])

  const online = state?.connected === true
  const waiting = state?.waiting === true && !online
  return <button
    type="button"
    className={[
      'dshDesktopRemoteEntry',
      wide ? 'dshDesktopRemoteEntryWide' : '',
      online ? 'dshDesktopRemoteEntryOnline' : waiting ? 'dshDesktopRemoteEntryWaiting' : '',
    ].filter(part => part !== '').join(' ')}
    aria-label={t('label')}
    title={online ? t('connected') : waiting ? t('waiting') : t('label')}
    onClick={open}
  >
    <Smartphone aria-hidden size={16} strokeWidth={1.5} />
    {wide ? <span className="dshDesktopRemoteEntryLabel">{t('label')}</span> : null}
  </button>
}

/** Claim the sidebar footer action ahead of the upstream bridge-next entry. */
export function applyDesktopRemoteEntry(ctx: ClientContext): void {
  installDesktopRemoteEntryStyles()
  ctx.effect(() => ctx.locale.register(DESKTOP_REMOTE_LOCALE_NAMESPACE, { zh, en }), 'dsh-plugin-desktop: remote entry locale')
  ctx.effect(() => ctx.slots.inject('sidebar.footer.action', () => ctx.slots.register({
    name: 'sidebar.footer.action',
    // Same id as the bridge-next registration so the list projection keeps
    // exactly one footer action; the lower priority plus this shell's earlier
    // ledger entry shadow the upstream modal flow without breaking its plugin.
    id: 'agents-anywhere-next',
    priority: -1,
    order: 26,
    label: () => ctx.locale.bind(DESKTOP_REMOTE_LOCALE_NAMESPACE)('label'),
    inject: () => ({
      t: ctx.locale.bind(DESKTOP_REMOTE_LOCALE_NAMESPACE),
    }) as never,
  }, DesktopRemoteEntry as never)), 'dsh-plugin-desktop: remote sidebar entry')
}
