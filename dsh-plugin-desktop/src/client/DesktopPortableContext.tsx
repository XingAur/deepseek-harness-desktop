/** Reviewable, opt-in export of the currently loaded text conversation. */
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Copy } from 'lucide-react'
import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type { ISessions } from '@deepseek-ai/dsh-api-session-controller/client'
import type {} from '@deepseek-ai/dsh-client-locale/client'
import type {} from '@deepseek-ai/dsh-client-ui-renderer/client'
import type { SessionId } from '@deepseek-ai/dsh-session/types'
import type { InjectFace, PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import type {} from '@deepseek-ai/dsh-client-ui-conversation/client'
import { portableContextDraft } from './desktop-portable-context.ts'
import { NS, en, zh, type DesktopPortableKey } from './desktop-portable-locales.ts'

async function writeClipboard(text: string): Promise<boolean> {
  try {
    if (!navigator.clipboard?.writeText) return false
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}

const STYLE_ID = 'dsh-portable-context-style'
const CSS = `
.dshPortableButton { display:inline-flex;align-items:center;gap:5px;border:1px solid var(--dsw-alias-border-l3);border-radius:8px;background:transparent;color:var(--dsw-alias-label-primary);padding:5px 9px;cursor:pointer;font:inherit }
.dshPortableButton:hover { background:var(--dsw-alias-interactive-bg-hover) }
.dshPortableBackdrop { position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,.55);display:grid;place-items:center;padding:20px }
.dshPortableDialog { box-sizing:border-box;display:flex;flex-direction:column;gap:12px;width:min(720px,100%);max-height:90vh;overflow:auto;padding:20px;border-radius:12px;background:var(--dsw-alias-surface-primary,#fff);color:var(--dsw-alias-label-primary,#222);box-shadow:0 12px 48px rgba(0,0,0,.3) }
.dshPortableDialog h2,.dshPortableDialog p { margin:0 }
.dshPortableDialog textarea { box-sizing:border-box;min-height:220px;max-height:50vh;resize:vertical;width:100%;padding:10px;border:1px solid var(--dsw-alias-border-l3);border-radius:8px;background:var(--dsw-alias-surface-primary,#fff);color:inherit;font:13px/1.5 monospace }
.dshPortableActions { display:flex;justify-content:flex-end;gap:10px }
.dshPortableActions button { cursor:pointer;padding:7px 12px }
.dshPortableAlert { color:var(--dsw-alias-state-warn-primary,#a35b00) }
`

interface PortableInjected {
  readDraft: (sessionId: SessionId) => { text: string; incomplete: boolean } | undefined
}

type PortableProps = PropsRuntime<'conversation.session.header.utilities'> & PropsLocale<typeof NS> & InjectFace<PortableInjected>

/** Explicit review and clipboard action; nothing leaves the browser before confirmation. */
export function DesktopPortableContext({ sessionId, readDraft, t }: PortableProps): ReactNode {
  const [draft, setDraft] = useState<string | null>(null)
  const [incomplete, setIncomplete] = useState(false)
  const [feedback, setFeedback] = useState<'copied' | 'failed' | null>(null)
  const editor = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    if (draft !== null) editor.current?.focus()
  }, [draft === null])
  useEffect(() => {
    if (draft === null) return
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === 'Escape') setDraft(null)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => { window.removeEventListener('keydown', onKeyDown) }
  }, [draft])
  return <>
    <button type="button" className="dshPortableButton" onClick={() => {
      const result = readDraft(sessionId)
      setDraft(result?.text ?? '')
      setIncomplete(result?.incomplete ?? true)
      setFeedback(null)
    }}><Copy size={14} aria-hidden />{t('action')}</button>
    {draft !== null && <div className="dshPortableBackdrop" onMouseDown={event => {
      if (event.target === event.currentTarget) setDraft(null)
    }}>
      <section className="dshPortableDialog" role="dialog" aria-modal="true" aria-label={t('title')}>
        <h2>{t('title')}</h2>
        <p>{t('warning')}</p>
        {incomplete && <p className="dshPortableAlert">{t('partial')}</p>}
        {draft === '' && <p>{t('empty')}</p>}
        <textarea ref={editor} aria-label={t('editor')} value={draft} onChange={event => { setDraft(event.target.value); setFeedback(null) }} />
        {feedback && <p role="status">{t(feedback)}</p>}
        <div className="dshPortableActions">
          <button type="button" onClick={() => { setDraft(null) }}>{t('cancel')}</button>
          <button type="button" disabled={draft.trim() === ''} onClick={() => {
            void writeClipboard(draft).then(ok => { setFeedback(ok ? 'copied' : 'failed') })
          }}>{t('copy')}</button>
        </div>
      </section>
    </div>}
  </>
}

declare module '@deepseek-ai/dsh-client-ui-slots' {
  interface LocaleNamespaceMap {
    'desktop.portable': DesktopPortableKey
  }
}

/** Contribute the current Session review action through the upstream header slot. */
export function applyDesktopPortableContext(ctx: ClientContext): void {
  if (typeof document !== 'undefined' && document.getElementById(STYLE_ID) === null) {
    const style = document.createElement('style')
    style.id = STYLE_ID
    style.textContent = CSS
    document.head.appendChild(style)
  }
  ctx.effect(() => ctx.locale.register(NS, { zh, en }), 'dsh-plugin-desktop: portable context locale')
  ctx.slots.inject('conversation.session.header.utilities', () => ctx.slots.register({
    name: 'conversation.session.header.utilities',
    id: 'desktop-portable-context',
    order: 30,
    locale: NS,
    inject: (): PortableInjected => ({
      readDraft: sessionId => {
        // This package builds Host and Client faces together; Host's sessions type shares the Context key.
        const binding = (ctx.sessions as unknown as ISessions).binding(sessionId)
        return binding === undefined ? undefined : portableContextDraft(binding.eventSource.getSnapshot())
      },
    }),
  }, DesktopPortableContext))
}
