/** Bottom sheets for permission mode, model & thinking level, context usage, rename. */

import { useState, type ReactNode } from 'react'
import { Check, LoaderCircle, X } from 'lucide-react'
import type { MobileCopy, formatTokens as formatTokensFn } from './copy.ts'
import type { ModelCatalog, ModelSelectionValue, PermissionOption, SessionInfo } from './types.ts'

type FormatTokens = typeof formatTokensFn

/** Shared bottom-sheet chrome: dimmed overlay, rounded card, slide-up animation. */
export function Sheet({ title, onClose, children }: { readonly title: string; readonly onClose: () => void; readonly children: ReactNode }): ReactNode {
  return <div className="fixed inset-0 z-30 flex flex-col justify-end" role="dialog" aria-label={title}>
    <button aria-label="close" className="absolute inset-0 bg-black/40 backdrop-blur-[2px]" onClick={onClose} type="button" />
    <div className="dshMobileSheetUp relative mx-auto w-full max-w-md rounded-t-2xl border-t border-border bg-background px-4 pb-[max(1rem,env(safe-area-inset-bottom))] pt-3 shadow-2xl">
      <div className="mx-auto mb-2 h-1 w-10 rounded-full bg-muted-foreground/25" />
      <div className="flex items-center gap-2 pb-2">
        <p className="min-w-0 flex-1 truncate text-sm font-semibold text-foreground">{title}</p>
        <button aria-label="close" className="flex size-8 items-center justify-center rounded-full text-muted-foreground active:bg-muted" onClick={onClose} type="button">
          <X aria-hidden className="size-4" />
        </button>
      </div>
      <div className="max-h-[70vh] overflow-y-auto pb-1">{children}</div>
    </div>
  </div>
}

function RowCheck({ visible }: { readonly visible: boolean }): ReactNode {
  return <span className="flex size-5 shrink-0 items-center justify-center">{visible ? <Check aria-hidden className="size-4 text-primary" /> : null}</span>
}

export function PermissionSheet({ options, currentValue, busy, onSelect }: {
  readonly options: readonly PermissionOption[]
  readonly currentValue: string
  readonly busy: boolean
  readonly onSelect: (preset: string) => void
}): ReactNode {
  return <div className="flex flex-col gap-1.5 pb-2">
    {options.length === 0 ? <p className="py-6 text-center text-sm text-muted-foreground">—</p> : null}
    {options.map(option => {
      const selected = option.value === currentValue
      return <button
        className={`flex w-full items-start gap-2.5 rounded-xl border px-3.5 py-3 text-left transition-colors active:bg-muted/70 ${selected ? 'border-primary/60 bg-primary/5' : 'border-border/70 bg-card'}`}
        disabled={busy || option.value === 'custom'}
        key={option.value}
        onClick={() => { onSelect(option.value) }}
        type="button"
      >
        <RowCheck visible={selected} />
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium leading-5 text-foreground">{option.name}</span>
          {option.description !== undefined ? <span className="mt-0.5 block text-xs leading-4 text-muted-foreground">{option.description}</span> : null}
        </span>
        {busy && selected ? <LoaderCircle aria-hidden className="mt-1 size-4 animate-spin text-muted-foreground" /> : null}
      </button>
    })}
  </div>
}

export function ModelSheet({ catalog, current, copy, busy, onApply }: {
  readonly catalog: ModelCatalog | null
  readonly current: ModelSelectionValue | null
  readonly copy: MobileCopy
  readonly busy: boolean
  readonly onApply: (selection: ModelSelectionValue) => void
}): ReactNode {
  const [provider, setProvider] = useState(current?.provider ?? '')
  const [model, setModel] = useState(current?.model ?? '')
  const [effort, setEffort] = useState(current?.reasoningEffort ?? '')
  const groups = catalog?.groups ?? []
  const chosen = groups
    .find(group => group.id === provider)?.models
    .find(row => row.id === model)
  const efforts = chosen?.reasoning?.efforts ?? []
  const pick = (nextProvider: string, nextModel: string, nextEfforts: ReadonlyArray<{ readonly id: string }>, defaultEffort: string | undefined): void => {
    setProvider(nextProvider)
    setModel(nextModel)
    setEffort(nextEfforts.length > 0 ? (defaultEffort ?? nextEfforts[0]?.id ?? '') : '')
  }
  return <div className="flex flex-col gap-3 pb-2">
    {groups.length === 0 ? <p className="py-6 text-center text-sm text-muted-foreground">{copy.catalogEmpty}</p> : null}
    {groups.map(group => <div key={group.id}>
      <p className="px-1 pb-1.5 pt-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{group.name}</p>
      <div className="flex flex-col gap-1.5">
        {group.models.map(row => {
          const selected = row.id === model && group.id === provider
          return <button
            className={`flex w-full items-center gap-2.5 rounded-xl border px-3.5 py-2.5 text-left transition-colors active:bg-muted/70 ${selected ? 'border-primary/60 bg-primary/5' : 'border-border/70 bg-card'}`}
            key={`${group.id}-${row.id}`}
            onClick={() => { pick(group.id, row.id, row.reasoning?.efforts ?? [], row.reasoning?.defaultEffort) }}
            type="button"
          >
            <RowCheck visible={selected} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium leading-5 text-foreground">{row.name}</span>
              <span className="block truncate text-xs leading-4 text-muted-foreground">{row.id}</span>
            </span>
            {row.reasoning !== undefined ? <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[10px] font-medium leading-4 text-muted-foreground">{copy.thinkingLevel}</span> : null}
          </button>
        })}
      </div>
    </div>)}
    {efforts.length > 0 && model !== ''
      ? <div className="rounded-xl border border-border/70 bg-card px-3.5 py-3">
          <p className="pb-2 text-xs font-semibold text-muted-foreground">{copy.thinkingLevel}</p>
          <div className="flex flex-wrap gap-1.5">
            {efforts.map(level => {
              const selected = level.id === effort
              return <button
                className={`rounded-full border px-3 py-1.5 text-xs font-medium transition-colors active:bg-muted ${selected ? 'border-primary/60 bg-primary/10 text-foreground' : 'border-border text-muted-foreground'}`}
                key={level.id}
                onClick={() => { setEffort(level.id) }}
                type="button"
              >
                {level.name}
              </button>
            })}
          </div>
        </div>
      : null}
    {(catalog?.failures ?? []).length > 0
      ? <p className="px-1 text-xs leading-4 text-muted-foreground/70">
          {copy.actionFailed}
        </p>
      : null}
    <button
      className={`mt-1 flex h-11 w-full items-center justify-center gap-2 rounded-xl text-sm font-semibold transition-all active:scale-[0.98] ${model === '' ? 'bg-muted text-muted-foreground/40' : 'bg-primary text-primary-foreground shadow-sm'}`}
      disabled={busy || model === ''}
      onClick={() => {
        if (model === '' || provider === '') return
        onApply(effort === '' ? { provider, model } : { provider, model, reasoningEffort: effort })
      }}
      type="button"
    >
      {busy ? <LoaderCircle aria-hidden className="size-4 animate-spin" /> : null}
      {copy.apply}
    </button>
  </div>
}

/** CSP-safe progress bar: SVG geometry attributes, never a style attribute. */
function UsageBar({ percent }: { readonly percent: number }): ReactNode {
  const clamped = Math.max(0, Math.min(100, percent))
  return <svg aria-hidden className="h-2 w-full" viewBox="0 0 100 8" preserveAspectRatio="none">
    <rect className="fill-muted-foreground/15" height="8" rx="4" width="100" y="0" />
    <rect className={clamped >= 90 ? 'fill-red-500' : 'fill-primary'} height="8" rx="4" width={clamped} y="0" />
  </svg>
}

function StatRow({ label, value }: { readonly label: string; readonly value: string }): ReactNode {
  return <div className="flex items-center justify-between rounded-lg bg-muted/50 px-3 py-2 text-[13px]">
    <span className="text-muted-foreground">{label}</span>
    <span className="font-medium tabular-nums text-foreground">{value}</span>
  </div>
}

export function ContextSheet({ info, copy, formatTokens }: {
  readonly info: SessionInfo
  readonly copy: MobileCopy
  readonly formatTokens: FormatTokens
}): ReactNode {
  const context = info.context
  return <div className="flex flex-col gap-3 pb-2">
    <div className="rounded-xl border border-border/70 bg-card px-3.5 py-3">
      <div className="flex items-baseline justify-between pb-2">
        <p className="text-[13px] font-medium text-foreground">{copy.contextUsed}</p>
        <p className="text-sm font-semibold tabular-nums text-foreground">
          {context?.percent !== null && context?.percent !== undefined ? `${String(context.percent)}%` : '—'}
        </p>
      </div>
      <UsageBar percent={context?.percent ?? 0} />
      <p className="pt-2 text-xs leading-4 text-muted-foreground">
        {`${context?.usedTokens !== null && context?.usedTokens !== undefined ? formatTokens(context.usedTokens) : '—'} / ${context?.contextWindow !== null && context?.contextWindow !== undefined ? formatTokens(context.contextWindow) : '—'} ${copy.contextTokens}`}
      </p>
    </div>
    {info.tokens !== null
      ? <div className="flex flex-col gap-1.5">
          <StatRow label={copy.inputTokens} value={formatTokens(info.tokens.inputTokens)} />
          <StatRow label={copy.outputTokens} value={formatTokens(info.tokens.outputTokens)} />
          <StatRow label={copy.cacheRead} value={formatTokens(info.tokens.cacheReadTokens)} />
          <StatRow label={copy.cacheWrite} value={formatTokens(info.tokens.cacheWriteTokens)} />
        </div>
      : null}
    {info.queue.length > 0
      ? <StatRow label={copy.queuedPrompt} value={String(info.queue.length)} />
      : null}
  </div>
}

export function RenameSheet({ initial, copy, busy, onSave }: {
  readonly initial: string
  readonly copy: MobileCopy
  readonly busy: boolean
  readonly onSave: (title: string) => void
}): ReactNode {
  const [value, setValue] = useState(initial)
  const trimmed = value.trim()
  return <div className="flex flex-col gap-3 pb-2">
    <input
      autoFocus
      className="h-11 w-full rounded-xl border border-border bg-card px-3.5 text-base text-foreground outline-none focus:border-ring focus:ring-1 focus:ring-ring/40"
      onChange={event => { setValue(event.target.value) }}
      value={value}
    />
    <button
      className={`flex h-11 w-full items-center justify-center gap-2 rounded-xl text-sm font-semibold transition-all active:scale-[0.98] ${trimmed === '' ? 'bg-muted text-muted-foreground/40' : 'bg-primary text-primary-foreground shadow-sm'}`}
      disabled={busy || trimmed === ''}
      onClick={() => { onSave(trimmed) }}
      type="button"
    >
      {busy ? <LoaderCircle aria-hidden className="size-4 animate-spin" /> : null}
      {copy.renameSave}
    </button>
  </div>
}
