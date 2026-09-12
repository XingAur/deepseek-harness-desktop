/** Bottom composer: auto-growing textarea with an inline tool rail.
 *
 * Layout mirrors the ZCode phone page: the textarea grows with its content
 * inside one rounded card; the icon rail (attachment, permissions, model,
 * context) and the send/stop button sit in a bottom row inside that card.
 */

import { useEffect, useRef, type ChangeEvent, type ReactNode } from 'react'
import { Gauge, LoaderCircle, Paperclip, Send, ShieldCheck, Square, X } from 'lucide-react'
import type { MobileCopy } from './copy.ts'
import type { QueueEntry } from './types.ts'

export interface ComposerAttachment {
  readonly mediaType: string
  readonly data: string
  readonly name: string
}

const MAX_ATTACHMENTS = 4

export function Composer(props: {
  readonly copy: MobileCopy
  readonly draft: string
  readonly onDraft: (value: string) => void
  readonly attachments: readonly ComposerAttachment[]
  readonly onAttachments: (value: readonly ComposerAttachment[]) => void
  readonly busy: boolean
  readonly running: boolean
  readonly placeholder: string
  readonly permissionLabel: string | null
  readonly modelLabel: string | null
  readonly effortLabel: string | null
  readonly contextPercent: number | null
  readonly queue: readonly QueueEntry[]
  readonly onSend: () => void
  readonly onStop: () => void
  readonly onOpenPermissions: () => void
  readonly onOpenModel: () => void
  readonly onOpenEffort: () => void
  readonly onOpenContext: () => void
  readonly onRemoveQueue: (itemId: string) => void
}): ReactNode {
  const {
    copy, draft, onDraft, attachments, onAttachments, busy, running, placeholder,
    permissionLabel, modelLabel, effortLabel, contextPercent, queue,
    onSend, onStop, onOpenPermissions, onOpenModel, onOpenEffort, onOpenContext, onRemoveQueue,
  } = props
  const textarea = useRef<HTMLTextAreaElement | null>(null)

  // Auto-grow: one row by default, taller as content wraps. Setting height
  // through the CSSOM (element.style) is not blocked by style-src CSP, which
  // governs inline style attributes and <style> elements only.
  useEffect(() => {
    const el = textarea.current
    if (el === null) return
    el.style.height = 'auto'
    el.style.height = `${String(Math.min(el.scrollHeight, 160))}px`
  }, [draft])

  const canSend = !busy && (draft.trim().length > 0 || attachments.length > 0)

  const pickFiles = (event: ChangeEvent<HTMLInputElement>): void => {
    const files = Array.from(event.target.files ?? []).filter(file => file.type.startsWith('image/'))
    const room = MAX_ATTACHMENTS - attachments.length
    const accepted = files.slice(0, Math.max(0, room))
    event.target.value = ''
    void Promise.all(accepted.map(async file => await new Promise<ComposerAttachment | null>(resolve => {
      const reader = new FileReader()
      reader.onload = () => {
        const result = typeof reader.result === 'string' ? reader.result : ''
        const comma = result.indexOf(',')
        const data = comma >= 0 ? result.slice(comma + 1) : ''
        resolve(data === '' ? null : { mediaType: file.type, data, name: file.name })
      }
      reader.onerror = () => { resolve(null) }
      reader.readAsDataURL(file)
    }))).then(parsed => {
      const added = parsed.filter((row): row is ComposerAttachment => row !== null)
      if (added.length > 0) onAttachments([...attachments, ...added])
    })
  }

  return <form
    className="sticky bottom-0 z-10 border-t border-border/60 bg-background/95 px-3 pb-[max(0.6rem,env(safe-area-inset-bottom))] pt-2.5 backdrop-blur"
    onSubmit={submitEvent => { submitEvent.preventDefault(); if (canSend) onSend() }}
  >
    {queue.length > 0
      ? <div className="mb-2 flex flex-col gap-1.5">
          {queue.map(entry => <div className="flex items-center gap-2 rounded-lg border border-border/60 bg-muted/50 px-2.5 py-1.5" key={entry.id}>
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-amber-500" />
            <span className="min-w-0 flex-1 truncate text-xs leading-4 text-muted-foreground">{entry.preview === '' ? copy.queuedPrompt : entry.preview}</span>
            <button aria-label={copy.removeQueue} className="flex size-6 shrink-0 items-center justify-center rounded-full text-muted-foreground active:bg-muted" onClick={() => { onRemoveQueue(entry.id) }} type="button">
              <X aria-hidden className="size-3.5" />
            </button>
          </div>)}
        </div>
      : null}
    <div className="rounded-2xl border border-border bg-card px-3 pb-1.5 pt-2.5 shadow-sm focus-within:border-ring focus-within:ring-1 focus-within:ring-ring/40">
      {attachments.length > 0
        ? <div className="mb-2 flex gap-2 overflow-x-auto pb-0.5">
            {attachments.map((file, index) => <span className="relative shrink-0" key={`${file.name}-${String(index)}`}>
              <img alt={file.name} className="size-16 rounded-lg border border-border/60 object-cover" src={`data:${file.mediaType};base64,${file.data}`} />
              <button aria-label={file.name} className="absolute -right-1.5 -top-1.5 flex size-5 items-center justify-center rounded-full bg-foreground text-background shadow" onClick={() => { onAttachments(attachments.filter((_, i) => i !== index)) }} type="button">
                <X aria-hidden className="size-3" />
              </button>
            </span>)}
          </div>
        : null}
      <textarea
        className="block max-h-40 w-full resize-none bg-transparent text-base leading-6 text-foreground outline-none placeholder:text-muted-foreground/70"
        onChange={event => { onDraft(event.target.value) }}
        placeholder={placeholder}
        ref={textarea}
        rows={1}
        value={draft}
      />
      <div className="flex items-center gap-1 py-1">
        <label className="flex size-8 shrink-0 cursor-pointer items-center justify-center rounded-full text-muted-foreground active:bg-muted">
          <Paperclip aria-hidden className="size-4" />
          <input accept="image/*" className="hidden" multiple onChange={pickFiles} type="file" />
        </label>
        {permissionLabel !== null
          ? <button aria-label={`${copy.permission}: ${permissionLabel}`} className="flex size-8 shrink-0 items-center justify-center rounded-full text-muted-foreground active:bg-muted" onClick={onOpenPermissions} type="button">
              <ShieldCheck aria-hidden className="size-4" />
            </button>
          : null}
        <span className="min-w-2 flex-1" />
        {contextPercent !== null
          ? <button aria-label={`${copy.contextTitle} ${String(contextPercent)}%`} className={`flex size-8 shrink-0 items-center justify-center rounded-full active:bg-muted ${contextPercent >= 90 ? 'text-red-600 dark:text-red-400' : 'text-muted-foreground'}`} onClick={onOpenContext} type="button">
              <Gauge aria-hidden className="size-4" />
            </button>
          : null}
        {modelLabel !== null
          ? <button aria-label={copy.model} className="flex h-8 max-w-28 shrink items-center gap-1 rounded-full px-2 text-[11px] font-medium text-foreground/80 active:bg-muted" onClick={onOpenModel} type="button">
              <span aria-hidden className="text-[10px]">▣</span>
              <span className="truncate">{modelLabel}</span>
            </button>
          : null}
        {effortLabel !== null
          ? <button aria-label={copy.thinkingLevel} className="flex h-8 max-w-20 shrink items-center rounded-full px-2 text-[11px] font-medium text-foreground/80 active:bg-muted" onClick={onOpenEffort} type="button">
              <span className="truncate">{effortLabel}</span>
            </button>
          : null}
        {running
          ? <button aria-label={copy.cancel} className="ml-0.5 flex size-8 shrink-0 items-center justify-center rounded-full bg-foreground text-background active:scale-95" onClick={onStop} type="button">
              <Square aria-hidden className="size-3.5 fill-current" />
            </button>
          : <button
              aria-label={copy.send}
              className={`ml-0.5 flex size-8 shrink-0 items-center justify-center rounded-full transition-all ${canSend ? 'bg-primary text-primary-foreground shadow-sm active:scale-95' : 'bg-muted text-muted-foreground/40'}`}
              disabled={!canSend}
              type="submit"
            >
              {busy ? <LoaderCircle aria-hidden className="size-4 animate-spin" /> : <Send aria-hidden className="size-4" />}
            </button>}
      </div>
    </div>
  </form>
}
