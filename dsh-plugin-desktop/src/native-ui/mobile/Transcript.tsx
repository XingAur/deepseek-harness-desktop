/** Rich transcript rendering: balloons, collapsible reasoning, tool rows, task cards. */

import type { ReactNode } from 'react'
import {
  Brain,
  Circle,
  CircleCheck,
  CircleDashed,
  FilePen,
  FileText,
  LoaderCircle,
  Terminal,
  Wrench,
} from 'lucide-react'
import type { MobileCopy, } from './copy.ts'
import type { DiffSummary, TodoRow, TranscriptItem } from './types.ts'

function durationText(ms: number): string {
  const seconds = Math.max(1, Math.round(ms / 1000))
  if (seconds < 60) return `${String(seconds)}s`
  return `${String(Math.floor(seconds / 60))}m${String(seconds % 60)}s`
}

/** One chat balloon: user on the right in primary, assistant on the left in card. */
function ChatBalloon({ kind, text }: { readonly kind: 'user' | 'assistant'; readonly text: string }): ReactNode {
  const mine = kind === 'user'
  return <div className={`flex w-full ${mine ? 'justify-end' : 'justify-start'}`}>
    <div className={`max-w-[86%] whitespace-pre-wrap break-words rounded-2xl px-3.5 py-2.5 text-[15px] leading-6 shadow-sm ${mine
      ? 'rounded-br-md bg-primary text-primary-foreground'
      : 'rounded-bl-md border border-border/60 bg-card text-foreground'}`}>
      {text}
    </div>
  </div>
}

/** Collapsible reasoning node rendered with the native details element (CSP-safe). */
function ReasoningNode({ text, copy }: { readonly text: string; readonly copy: MobileCopy }): ReactNode {
  return <details className="group rounded-xl border border-border/60 bg-muted/40">
    <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs font-medium text-muted-foreground [&::-webkit-details-marker]:hidden">
      <Brain aria-hidden className="size-3.5" />
      <span className="flex-1 text-left">{copy.reasoning}</span>
      <span className="text-muted-foreground/50 transition-transform group-open:rotate-180">⌄</span>
    </summary>
    <p className="max-h-64 overflow-y-auto whitespace-pre-wrap break-words border-t border-border/50 px-3 py-2.5 text-[13px] leading-5 text-muted-foreground">
      {text}
    </p>
  </details>
}

function toolIcon(name: string): ReactNode {
  if (name === 'bash' || name === 'pwsh' || name.includes('terminal')) return <Terminal aria-hidden className="size-3.5 shrink-0" />
  if (name === 'write' || name === 'edit' || name.includes('editor')) return <FilePen aria-hidden className="size-3.5 shrink-0" />
  if (name === 'read' || name.includes('search') || name.includes('glob')) return <FileText aria-hidden className="size-3.5 shrink-0" />
  return <Wrench aria-hidden className="size-3.5 shrink-0" />
}

function statusBadge(item: Extract<TranscriptItem, { kind: 'tool' }>, copy: MobileCopy): ReactNode {
  if (item.status === 'running') {
    return <LoaderCircle aria-label={copy.toolRunning} className="size-3.5 shrink-0 animate-spin text-muted-foreground/70" />
  }
  if (item.status === 'error') return <CircleAlertInline />
  return <CircleCheck aria-hidden className="size-3.5 shrink-0 text-emerald-500" />
}

function CircleAlertInline(): ReactNode {
  return <span aria-hidden className="flex size-3.5 shrink-0 items-center justify-center rounded-full bg-red-500/15 text-[10px] font-bold leading-none text-red-500">!</span>
}

function DiffBadges({ diffs, copy }: { readonly diffs: readonly DiffSummary[]; readonly copy: MobileCopy }): ReactNode {
  return <div className="mt-1.5 flex flex-col gap-1">
    {diffs.map(diff => <span key={diff.path} className="flex min-w-0 items-center gap-1.5 text-[11px] leading-4 text-muted-foreground">
      <span className="truncate font-mono">{diff.path}</span>
      {diff.added !== null && diff.added > 0 ? <span className="shrink-0 rounded bg-emerald-500/15 px-1 font-medium tabular-nums text-emerald-600 dark:text-emerald-400">{`${copy.added}${String(diff.added)}`}</span> : null}
      {diff.removed !== null && diff.removed > 0 ? <span className="shrink-0 rounded bg-red-500/15 px-1 font-medium tabular-nums text-red-600 dark:text-red-400">{`${copy.removed}${String(diff.removed)}`}</span> : null}
      {diff.added === null && diff.removed === null ? <span className="shrink-0 text-muted-foreground/70">{copy.edited}</span> : null}
    </span>)}
  </div>
}

/** One tool call row: icon, title, live status, duration, diff counts. */
function ToolRow({ item, copy }: { readonly item: Extract<TranscriptItem, { kind: 'tool' }>; readonly copy: MobileCopy }): ReactNode {
  return <div className="flex flex-col rounded-xl border border-border/60 bg-card px-3 py-2.5">
    <div className="flex min-w-0 items-center gap-2 text-[13px] leading-5">
      {toolIcon(item.name)}
      <span className="min-w-0 flex-1 truncate font-mono text-foreground/90">{item.title}</span>
      {item.end !== null ? <span className="shrink-0 text-[11px] tabular-nums text-muted-foreground/70">{durationText(item.end - item.time)}</span> : null}
      {statusBadge(item, copy)}
    </div>
    {item.diffs !== null ? <DiffBadges diffs={item.diffs} copy={copy} /> : null}
  </div>
}

function TodoStatus({ status }: { readonly status: TodoRow['status'] }): ReactNode {
  if (status === 'completed') return <CircleCheck aria-hidden className="size-4 shrink-0 text-emerald-500" />
  if (status === 'in_progress') return <LoaderCircle aria-hidden className="size-4 shrink-0 animate-spin text-amber-500" />
  return status === 'pending'
    ? <Circle aria-hidden className="size-4 shrink-0 text-muted-foreground/50" />
    : <CircleDashed aria-hidden className="size-4 shrink-0 text-muted-foreground/50" />
}

/** Task-list snapshot card, the mobile take on the desktop task bar. */
function TodoCard({ todos, copy }: { readonly todos: readonly TodoRow[]; readonly copy: MobileCopy }): ReactNode {
  const done = todos.filter(todo => todo.status === 'completed').length
  return <div className="rounded-xl border border-border/60 bg-card px-3 py-2.5">
    <p className="flex items-center gap-2 pb-1.5 text-xs font-semibold text-foreground/90">
      <CircleCheck aria-hidden className="size-3.5 text-emerald-500" />
      {copy.taskPlan}
      <span className="ml-auto rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium leading-4 tabular-nums text-muted-foreground">{`${String(done)}/${String(todos.length)}`}</span>
    </p>
    <ol className="flex flex-col gap-1">
      {todos.map((todo, index) => <li key={`${String(index)}-${todo.content}`} className="flex items-start gap-2 text-[13px] leading-5">
        <span className="mt-0.5"><TodoStatus status={todo.status} /></span>
        <span className={`min-w-0 break-words ${todo.status === 'completed' ? 'text-muted-foreground/60 line-through' : 'text-foreground/90'}`}>{todo.content}</span>
      </li>)}
    </ol>
  </div>
}

/** The client's thinking row: pulsing loader, bouncing dots, dimmed label. */
export function ThinkingRow({ label }: { readonly label: string }): ReactNode {
  return <div className="flex items-center gap-2.5 px-1 py-3 text-sm text-muted-foreground" aria-live="polite">
    <LoaderCircle aria-hidden className="size-4 animate-spin text-muted-foreground/70" />
    <span className="flex items-center gap-1">
      <span className="dshMobileThinkingDot size-1.5 rounded-full bg-muted-foreground/70" />
      <span className="dshMobileThinkingDot size-1.5 rounded-full bg-muted-foreground/70" />
      <span className="dshMobileThinkingDot size-1.5 rounded-full bg-muted-foreground/70" />
    </span>
    {label}…
  </div>
}

export function TranscriptView({ items, copy }: { readonly items: readonly TranscriptItem[]; readonly copy: MobileCopy }): ReactNode {
  return <div className="flex flex-col gap-2.5">
    {items.map((item, index) => {
      const key = `${item.kind}-${String(item.time)}-${String(index)}`
      if (item.kind === 'user') return <ChatBalloon key={key} kind="user" text={item.text} />
      if (item.kind === 'assistant') return <ChatBalloon key={key} kind="assistant" text={item.text} />
      if (item.kind === 'reasoning') return <ReasoningNode key={key} text={item.text} copy={copy} />
      if (item.kind === 'tool') return <ToolRow key={key} item={item} copy={copy} />
      return <TodoCard key={key} todos={item.todos} copy={copy} />
    })}
  </div>
}
