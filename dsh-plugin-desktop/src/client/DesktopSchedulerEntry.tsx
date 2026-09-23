/**
 * Sidebar scheduled-task entry: a control that mirrors the New Session
 * button's geometry, placed directly above it. The footer.action slot is the
 * only list hole the sidebar shell exposes, so this registrant portals the
 * control into the column above New Session; clicking it opens the in-app
 * task dialog instead of the native editor window.
 */

import { useEffect, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { CalendarClock } from 'lucide-react'
import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type { PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import type {} from '@deepseek-ai/dsh-client-ui-sidebar/client'
import { DesktopSchedulerModal, type DesktopSchedulerTranslate } from './DesktopSchedulerModal.tsx'

const SCHEDULER_LOCALE_NAMESPACE = 'desktop.scheduler'
const ANCHOR_ID = 'dsh-desktop-scheduler-anchor'

/** Renderer-composed props for the sidebar scheduler entry. */
export type DesktopSchedulerEntryProps = PropsRuntime<'sidebar.footer.action'> & {
  readonly t: DesktopSchedulerTranslate
}

const STYLE_ID = 'dsh-desktop-scheduler-entry-styles'
const CSS = `
.dshDesktopSchedulerEntry {
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 38px;
  padding: 8px 16px;
  margin: 0 2px 8px;
  box-sizing: border-box;
  border: 0.5px solid var(--dsw-alias-border-l3);
  border-radius: 12px;
  background: var(--dsw-alias-button-elevated-fill);
  color: var(--dsw-alias-label-primary);
  font: inherit;
  font-size: 14px;
  font-weight: 500;
  line-height: 22px;
  cursor: pointer;
  overflow: hidden;
}
.dshDesktopSchedulerEntry:hover { background: var(--dsw-alias-button-floating-hover); }
.dshDesktopSchedulerEntry:focus-visible { outline: 2px solid var(--dsw-alias-brand-primary); outline-offset: 1px; }
.dshDesktopSchedulerEntryLabel { max-width: 200px; overflow: hidden; white-space: nowrap; }
.dshDesktopSchedulerEntryRail {
  align-self: flex-start;
  width: 36px;
  height: 36px;
  padding: 0;
  margin: 0 0 12px;
  gap: 0;
  border-color: transparent;
  background: transparent;
}
.dshDesktopSchedulerEntryRail:hover { background: var(--dsw-alias-interactive-bg-hover); }
.dshDesktopSchedulerEntryRail .dshDesktopSchedulerEntryLabel { display: none; }
`

function installDesktopSchedulerEntryStyles(): void {
  if (typeof document === 'undefined') return
  if (document.getElementById(STYLE_ID) !== null) return
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = CSS
  document.head.appendChild(style)
}

/**
 * The New Session control is the labeled button whose visible text is the
 * session.new copy; the expanded brand button reuses the same aria-label, so
 * the text match is what disambiguates.
 */
function findNewSessionButton(): HTMLButtonElement | undefined {
  const labeled = [...document.querySelectorAll<HTMLButtonElement>(
    'button[aria-label="新建会话"], button[aria-label="New session"]',
  )]
  const named = labeled.find(button => {
    const text = (button.textContent ?? '').replace(/\s+/gu, '')
    return text === '新会话' || text === 'NewSession'
  })
  if (named !== undefined) return named
  return labeled.length === 1 ? labeled[0] : labeled.at(-1)
}

function placeAnchor(): HTMLElement | undefined {
  const button = findNewSessionButton()
  const parent = button?.parentElement
  if (button === undefined || parent === null || parent === undefined) return undefined
  const existing = document.getElementById(ANCHOR_ID)
  if (existing !== null) {
    if (existing.nextElementSibling !== button) parent.insertBefore(existing, button)
    return existing
  }
  const host = document.createElement('div')
  host.id = ANCHOR_ID
  parent.insertBefore(host, button)
  return host
}

/** Claim a footer.action seat and present the entry above New Session. */
export function applyDesktopSchedulerEntry(ctx: ClientContext): void {
  installDesktopSchedulerEntryStyles()
  ctx.effect(() => ctx.slots.inject('sidebar.footer.action', () => ctx.slots.register({
    name: 'sidebar.footer.action',
    id: 'desktop-scheduler',
    order: 10,
    label: () => ctx.locale.bind(SCHEDULER_LOCALE_NAMESPACE)('nav'),
    inject: () => ({
      t: ctx.locale.bind(SCHEDULER_LOCALE_NAMESPACE),
    }) as never,
  }, DesktopSchedulerEntry as never)), 'dsh-plugin-desktop: scheduler sidebar entry')
}

/** Render the scheduled-task control above New Session, falling back to the footer. */
export function DesktopSchedulerEntry({ wide, t }: DesktopSchedulerEntryProps): ReactNode {
  const [anchor, setAnchor] = useState<HTMLElement>()
  const [open, setOpen] = useState(false)
  useEffect(() => {
    installDesktopSchedulerEntryStyles()
    const sync = (): void => { setAnchor(placeAnchor()) }
    sync()
    const observer = new MutationObserver(sync)
    observer.observe(document.body, { childList: true, subtree: true })
    return () => {
      observer.disconnect()
      document.getElementById(ANCHOR_ID)?.remove()
    }
  }, [])
  const control = (
    <>
      <button
        type="button"
        className={wide ? 'dshDesktopSchedulerEntry' : 'dshDesktopSchedulerEntry dshDesktopSchedulerEntryRail'}
        aria-label={t('nav')}
        title={t('nav')}
        onClick={() => { setOpen(true) }}
      >
        <CalendarClock aria-hidden size={wide ? 14 : 18} strokeWidth={1.5} />
        {wide ? <span className="dshDesktopSchedulerEntryLabel">{t('nav')}</span> : null}
      </button>
      {open && <DesktopSchedulerModal open={open} onClose={() => { setOpen(false) }} t={t} />}
    </>
  )
  if (anchor !== undefined) return createPortal(control, anchor)
  return control
}
