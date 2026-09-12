/** Session side panel: the plan document and the terminal command history. */

import type { ReactNode } from 'react'
import { ClipboardList, SquareTerminal, X } from 'lucide-react'
import type { MobileCopy } from './copy.ts'
import { MarkdownView } from './markdown.tsx'
import type { TranscriptItem } from './types.ts'

export type PanelTab = 'plan' | 'terminal'

type ToolEntry = Extract<TranscriptItem, { kind: 'tool' }>

function isTerminalTool(name: string): boolean {
  return name === 'bash' || name === 'pwsh' || name.includes('terminal')
}

function durationText(ms: number): string {
  const seconds = Math.max(1, Math.round(ms / 1000))
  if (seconds < 60) return `${String(seconds)}s`
  return `${String(Math.floor(seconds / 60))}m${String(seconds % 60)}s`
}

function TerminalRow({ item }: { readonly item: ToolEntry }): ReactNode {
  return <div className="flex flex-col gap-0.5 rounded-lg border border-border/60 bg-card px-3 py-2">
    <div className="flex min-w-0 items-center gap-2 text-[13px] leading-5">
      <span aria-hidden className="shrink-0 font-mono text-muted-foreground">$</span>
      <span className="min-w-0 flex-1 truncate font-mono text-foreground/90">{item.title}</span>
      {item.end !== null ? <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground/70">{durationText(item.end - item.time)}</span> : null}
      {item.status === 'running'
        ? <span aria-hidden className="size-2 shrink-0 animate-pulse rounded-full bg-amber-500" />
        : item.status === 'error'
          ? <span aria-hidden className="size-2 shrink-0 rounded-full bg-red-500" />
          : <span aria-hidden className="size-2 shrink-0 rounded-full bg-emerald-500" />}
    </div>
  </div>
}

export function SidePanel({ copy, tab, onTab, onClose, planMarkdown, transcript }: {
  readonly copy: MobileCopy
  readonly tab: PanelTab
  readonly onTab: (tab: PanelTab) => void
  readonly onClose: () => void
  readonly planMarkdown: string | null
  readonly transcript: readonly TranscriptItem[]
}): ReactNode {
  const terminalItems = transcript
    .filter((item): item is ToolEntry => item.kind === 'tool' && isTerminalTool(item.name))
    .slice()
    .reverse()
  return <div className="fixed inset-0 z-30 flex flex-col justify-end" role="dialog" aria-label={tab === 'plan' ? copy.planTab : copy.terminalTab}>
    <button aria-label="close" className="absolute inset-0 bg-black/40 backdrop-blur-[2px]" onClick={onClose} type="button" />
    <div className="dshMobileSheetUp relative mx-auto flex h-[88vh] w-full max-w-md flex-col rounded-t-2xl border-t border-border bg-background pb-[max(0.5rem,env(safe-area-inset-bottom))] shadow-2xl">
      <div className="mx-auto mt-2.5 h-1 w-10 shrink-0 rounded-full bg-muted-foreground/25" />
      <div className="flex items-center gap-2 border-b border-border/60 px-4 pb-2 pt-2">
        <div className="flex min-w-0 flex-1 items-center gap-1.5 overflow-x-auto">
          <button
            className={`flex h-8 shrink-0 items-center gap-1.5 rounded-full px-3 text-xs font-medium transition-colors ${tab === 'plan' ? 'bg-primary/10 text-foreground' : 'text-muted-foreground active:bg-muted'}`}
            onClick={() => { onTab('plan') }}
            type="button"
          >
            <ClipboardList aria-hidden className="size-3.5" />
            {copy.planTab}
          </button>
          <button
            className={`flex h-8 shrink-0 items-center gap-1.5 rounded-full px-3 text-xs font-medium transition-colors ${tab === 'terminal' ? 'bg-primary/10 text-foreground' : 'text-muted-foreground active:bg-muted'}`}
            onClick={() => { onTab('terminal') }}
            type="button"
          >
            <SquareTerminal aria-hidden className="size-3.5" />
            {copy.terminalTab}
          </button>
        </div>
        <button aria-label="close" className="flex size-8 shrink-0 items-center justify-center rounded-full text-muted-foreground active:bg-muted" onClick={onClose} type="button">
          <X aria-hidden className="size-4" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-3">
        {tab === 'plan'
          ? (planMarkdown === null || planMarkdown.trim() === ''
              ? <p className="pt-16 text-center text-sm text-muted-foreground">{copy.noPlan}</p>
              : <MarkdownView markdown={planMarkdown} />)
          : (terminalItems.length === 0
              ? <p className="pt-16 text-center text-sm text-muted-foreground">{copy.terminalEmpty}</p>
              : <div className="flex flex-col gap-1.5">{terminalItems.map(item => <TerminalRow item={item} key={item.callId} />)}</div>)}
      </div>
    </div>
  </div>
}
