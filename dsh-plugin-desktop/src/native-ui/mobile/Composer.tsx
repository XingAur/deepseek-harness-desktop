/** Bottom composer: tool rail (permissions/model/context/stop), queue chips, input. */

import type { ReactNode } from 'react'
import { Box, Gauge, LoaderCircle, Send, ShieldCheck, Square, X } from 'lucide-react'
import type { MobileCopy } from './copy.ts'
import type { QueueEntry } from './types.ts'

export function Composer({ copy, draft, onDraft, busy, running, placeholder, permissionLabel, modelLabel, contextPercent, queue, onSend, onStop, onOpenPermissions, onOpenModel, onOpenContext, onRemoveQueue }: {
  readonly copy: MobileCopy
  readonly draft: string
  readonly onDraft: (value: string) => void
  readonly busy: boolean
  readonly running: boolean
  readonly placeholder: string
  readonly permissionLabel: string | null
  readonly modelLabel: string | null
  readonly contextPercent: number | null
  readonly queue: readonly QueueEntry[]
  readonly onSend: () => void
  readonly onStop: () => void
  readonly onOpenPermissions: () => void
  readonly onOpenModel: () => void
  readonly onOpenContext: () => void
  readonly onRemoveQueue: (itemId: string) => void
}): ReactNode {
  return <form
    className="sticky bottom-0 z-10 border-t border-border/60 bg-background/95 px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-2.5 backdrop-blur"
    onSubmit={event => { event.preventDefault(); onSend() }}
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
    <div className="mb-2 flex items-center gap-1.5 overflow-x-auto pb-0.5">
      {permissionLabel !== null
        ? <button className="flex h-8 shrink-0 items-center gap-1.5 rounded-full border border-border bg-card px-2.5 text-[11px] font-medium text-foreground/90 active:bg-muted" onClick={onOpenPermissions} type="button">
            <ShieldCheck aria-hidden className="size-3.5 text-muted-foreground" />
            <span className="max-w-28 truncate">{permissionLabel}</span>
          </button>
        : null}
      {modelLabel !== null
        ? <button className="flex h-8 shrink-0 items-center gap-1.5 rounded-full border border-border bg-card px-2.5 text-[11px] font-medium text-foreground/90 active:bg-muted" onClick={onOpenModel} type="button">
            <Box aria-hidden className="size-3.5 text-muted-foreground" />
            <span className="max-w-28 truncate">{modelLabel}</span>
          </button>
        : null}
      {contextPercent !== null
        ? <button className={`flex h-8 shrink-0 items-center gap-1.5 rounded-full border px-2.5 text-[11px] font-medium active:bg-muted ${contextPercent >= 90 ? 'border-red-500/50 bg-red-500/10 text-red-600 dark:text-red-400' : 'border-border bg-card text-foreground/90'}`} onClick={onOpenContext} type="button">
            <Gauge aria-hidden className="size-3.5" />
            <span className="tabular-nums">{`${String(contextPercent)}%`}</span>
          </button>
        : null}
      {running
        ? <button aria-label={copy.cancel} className="ml-auto flex size-8 shrink-0 items-center justify-center rounded-full bg-foreground text-background active:scale-95" onClick={onStop} type="button">
            <Square aria-hidden className="size-3.5 fill-current" />
          </button>
        : null}
    </div>
    <div className="flex items-center gap-2 rounded-2xl border border-border bg-card px-3.5 py-1.5 shadow-sm focus-within:border-ring focus-within:ring-1 focus-within:ring-ring/40">
      <input
        className="h-11 min-w-0 flex-1 bg-transparent text-base text-foreground outline-none placeholder:text-muted-foreground/70"
        onChange={event => { onDraft(event.target.value) }}
        placeholder={placeholder}
        value={draft}
      />
      <button
        aria-label={copy.send}
        className={`flex size-11 shrink-0 items-center justify-center rounded-full transition-all ${draft.trim().length === 0 || busy ? 'bg-muted text-muted-foreground/40' : 'bg-primary text-primary-foreground shadow-sm active:scale-95'}`}
        disabled={busy || draft.trim().length === 0}
        type="submit"
      >
        {busy ? <LoaderCircle aria-hidden className="size-4 animate-spin" /> : <Send aria-hidden className="size-4" />}
      </button>
    </div>
  </form>
}
