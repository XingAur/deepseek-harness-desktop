/** Actionable approval and question cards for phone-side interruptions. */

import { useState, type ReactNode } from 'react'
import { Check, CircleHelp, LoaderCircle, ShieldQuestion } from 'lucide-react'
import type { MobileCopy } from './copy.ts'
import type { InterruptionRecord, MobileQuestion } from './types.ts'

export interface AnswerDraft {
  readonly id: string
  readonly selected: readonly string[]
  readonly custom?: string
}

function ApprovalCard({ record, copy, busy, onDecide }: {
  readonly record: InterruptionRecord
  readonly copy: MobileCopy
  readonly busy: boolean
  readonly onDecide: (key: string, action: 'allow' | 'reject' | 'delegate') => void
}): ReactNode {
  return <div className="rounded-xl border border-border/70 bg-card px-3.5 py-3 shadow-sm">
    <p className="flex items-center gap-2 text-[13px] font-semibold text-foreground">
      <ShieldQuestion aria-hidden className="size-4 shrink-0 text-amber-600 dark:text-amber-400" />
      {copy.interruptionApproval}
      <span className="min-w-0 flex-1 truncate font-mono text-xs font-normal text-muted-foreground">{record.toolName}</span>
    </p>
    {record.reason !== null && record.reason !== '' ? <p className="mt-1.5 whitespace-pre-wrap break-words text-xs leading-5 text-foreground/80">{record.reason}</p> : null}
    {record.delegated
      ? <p className="mt-2 text-xs font-medium text-muted-foreground">{copy.interruptionDelegated}</p>
      : <div className="mt-2.5 flex gap-2">
          <button className="flex h-9 flex-1 items-center justify-center gap-1.5 rounded-lg bg-primary text-xs font-semibold text-primary-foreground shadow-sm active:scale-[0.98] disabled:opacity-50" disabled={busy} onClick={() => { onDecide(record.key, 'allow') }} type="button">
            {busy ? <LoaderCircle aria-hidden className="size-3.5 animate-spin" /> : <Check aria-hidden className="size-3.5" />}
            {copy.allow}
          </button>
          <button className="flex h-9 flex-1 items-center justify-center rounded-lg bg-destructive text-xs font-semibold text-white shadow-sm active:scale-[0.98] disabled:opacity-50" disabled={busy} onClick={() => { onDecide(record.key, 'reject') }} type="button">
            {copy.reject}
          </button>
        </div>}
  </div>
}

function OptionRow({ label, description, selected, multi, onToggle }: {
  readonly label: string
  readonly description?: string
  readonly selected: boolean
  readonly multi: boolean
  readonly onToggle: () => void
}): ReactNode {
  return <button
    className={`flex w-full items-start gap-2.5 rounded-lg border px-3 py-2.5 text-left transition-colors active:bg-muted/60 ${selected ? 'border-primary/60 bg-primary/5' : 'border-border/60 bg-card'}`}
    onClick={onToggle}
    type="button"
  >
    <span className={`mt-0.5 flex size-4 shrink-0 items-center justify-center border ${multi ? 'rounded-[5px]' : 'rounded-full'} ${selected ? 'border-primary bg-primary text-primary-foreground' : 'border-muted-foreground/40'}`}>
      {selected ? <Check aria-hidden className="size-3" /> : null}
    </span>
    <span className="min-w-0 flex-1">
      <span className="block text-sm leading-5 text-foreground">{label}</span>
      {description !== undefined && description !== '' ? <span className="mt-0.5 block text-xs leading-4 text-muted-foreground">{description}</span> : null}
    </span>
  </button>
}

function QuestionBlock({ question, copy, selected, custom, onSelected, onCustom }: {
  readonly question: MobileQuestion
  readonly copy: MobileCopy
  readonly selected: readonly string[]
  readonly custom: string
  readonly onSelected: (labels: readonly string[]) => void
  readonly onCustom: (value: string) => void
}): ReactNode {
  const options = question.options ?? []
  const approveLabel = question.intent?.kind === 'plan-review' ? question.intent.approve : null
  const toggle = (label: string): void => {
    if (question.multiSelect === true) {
      onSelected(selected.includes(label) ? selected.filter(row => row !== label) : [...selected, label])
    } else {
      onSelected(selected.includes(label) && custom === '' ? [] : [label])
    }
  }
  return <div className="flex flex-col gap-2">
    {question.header !== undefined && question.header !== '' ? <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{question.header}</p> : null}
    <p className="whitespace-pre-wrap break-words text-sm font-medium leading-5 text-foreground">{question.question}</p>
    {question.detail !== undefined && question.detail !== ''
      ? <details className="group rounded-lg bg-muted/40">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 px-3 py-1.5 text-xs text-muted-foreground [&::-webkit-details-marker]:hidden">
            <span className="flex-1 text-left truncate">{question.detail.slice(0, 80)}</span>
            <span className="transition-transform group-open:rotate-180">⌄</span>
          </summary>
          <p className="max-h-56 overflow-y-auto whitespace-pre-wrap break-words border-t border-border/50 px-3 py-2 text-xs leading-5 text-muted-foreground">{question.detail}</p>
        </details>
      : null}
    {options.length > 0
      ? <div className="flex flex-col gap-1.5">
          {options.map(option => {
            const approve = option.label === approveLabel
            const props = {
              key: option.label,
              label: approve ? `${option.label} · ${copy.approvePlan}` : option.label,
              multi: question.multiSelect === true,
              onToggle: () => { toggle(option.label) },
              selected: selected.includes(option.label),
              ...(approve || option.description === undefined ? {} : { description: option.description }),
            }
            return <OptionRow {...props} />
          })}
        </div>
      : null}
    <div className="flex items-center gap-2 rounded-lg border border-border/60 bg-card px-3 py-1.5 focus-within:border-ring focus-within:ring-1 focus-within:ring-ring/40">
      <input
        className="h-9 min-w-0 flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground/60"
        onChange={event => { onCustom(event.target.value) }}
        placeholder={copy.otherAnswer}
        value={custom}
      />
    </div>
  </div>
}

function QuestionCard({ record, copy, busy, onAnswer }: {
  readonly record: InterruptionRecord
  readonly copy: MobileCopy
  readonly busy: boolean
  readonly onAnswer: (key: string, answers: readonly AnswerDraft[]) => void
}): ReactNode {
  const questions = record.questions ?? []
  const [selected, setSelected] = useState<Record<string, readonly string[]>>(() => Object.fromEntries(questions.map(question => [question.id, [] as string[]])))
  const [custom, setCustom] = useState<Record<string, string>>(() => Object.fromEntries(questions.map(question => [question.id, ''])))
  const ready = questions.every(question => (selected[question.id]?.length ?? 0) > 0 || custom[question.id] !== undefined && custom[question.id] !== '')
  if (record.delegated) {
    return <div className="rounded-xl border border-border/70 bg-card px-3.5 py-3 shadow-sm">
      <p className="flex items-center gap-2 text-[13px] font-semibold text-muted-foreground">
        <CircleHelp aria-hidden className="size-4 shrink-0" />
        {copy.interruptionQuestion}
      </p>
      <p className="mt-1.5 text-xs font-medium text-muted-foreground">{copy.interruptionDelegated}</p>
    </div>
  }
  return <div className="rounded-xl border border-border/70 bg-card px-3.5 py-3 shadow-sm">
    <p className="flex items-center gap-2 text-[13px] font-semibold text-foreground">
      <CircleHelp aria-hidden className="size-4 shrink-0 text-primary" />
      {questions.some(question => question.intent?.kind === 'plan-review') ? copy.planReview : copy.interruptionQuestion}
    </p>
    <div className="mt-2.5 flex flex-col gap-3.5">
      {questions.map(question => <QuestionBlock
        copy={copy}
        custom={custom[question.id] ?? ''}
        key={question.id}
        question={question}
        selected={selected[question.id] ?? []}
        onCustom={value => { setCustom(current => ({ ...current, [question.id]: value })) }}
        onSelected={labels => { setSelected(current => ({ ...current, [question.id]: labels })) }}
      />)}
    </div>
    <button
      className={`mt-3 flex h-10 w-full items-center justify-center gap-2 rounded-lg text-sm font-semibold transition-all active:scale-[0.98] ${ready ? 'bg-primary text-primary-foreground shadow-sm' : 'bg-muted text-muted-foreground/40'}`}
      disabled={busy || !ready}
      onClick={() => {
        onAnswer(record.key, questions.map(question => {
          const customText = custom[question.id] ?? ''
          const draft: AnswerDraft = { id: question.id, selected: selected[question.id] ?? [] }
          return customText === '' ? draft : { ...draft, custom: customText }
        }))
      }}
      type="button"
    >
      {busy ? <LoaderCircle aria-hidden className="size-4 animate-spin" /> : null}
      {copy.submitAnswer}
    </button>
  </div>
}

export function InterruptionCard({ record, copy, busy, onDecide, onAnswer }: {
  readonly record: InterruptionRecord
  readonly copy: MobileCopy
  readonly busy: boolean
  readonly onDecide: (key: string, action: 'allow' | 'reject' | 'delegate') => void
  readonly onAnswer: (key: string, answers: readonly AnswerDraft[]) => void
}): ReactNode {
  if (record.kind === 'approval') return <ApprovalCard busy={busy} copy={copy} onDecide={onDecide} record={record} />
  return <QuestionCard busy={busy} copy={copy} onAnswer={onAnswer} record={record} />
}
