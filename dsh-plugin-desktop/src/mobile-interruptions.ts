/**
 * Phone-answerable interruption registry for the mobile remote page.
 *
 * The Host exposes approvals (`approval/request`) and structured questions
 * (`user-questions/request`) as Cordis waterfalls: returning a value claims
 * the ask, calling `next()` delegates to the next answerer (the desktop
 * client's own bridge). This registry gives the phone a bounded first claim:
 * while a paired phone is actively polling, the waterfall answer is held so
 * the phone can decide; when the phone goes stale, the hold expires, or the
 * phone explicitly delegates, we fall through to the desktop unchanged.
 */

import { randomUUID } from 'node:crypto'

/** One selectable option of a structured question, as rendered on the phone. */
export interface MobileQuestionOption {
  readonly label: string
  readonly description?: string
}

/** One question of a structured ask, in the phone-safe subset the page needs. */
export interface MobileQuestion {
  readonly id: string
  readonly question: string
  readonly detail?: string
  readonly header?: string
  readonly options?: readonly MobileQuestionOption[]
  readonly multiSelect?: boolean
  /** Plan-review presentation intent; `approve` names the approving label. */
  readonly intent?: { readonly kind: 'plan-review'; readonly approve: string }
}

/** One answer the phone submitted for a question id. */
export interface MobileQuestionAnswerItem {
  readonly id: string
  readonly selected: readonly string[]
  readonly custom?: string
}

/** A pending interruption the phone can still claim or see. */
export interface MobileInterruption {
  readonly kind: 'approval' | 'question'
  readonly key: string
  readonly sessionId: string | null
  readonly toolName: string | null
  readonly callId: string | null
  readonly reason: string | null
  readonly questions: readonly MobileQuestion[] | null
  readonly at: number
  /** True once the hold fell through to the desktop answerer. */
  readonly delegated: boolean
}

/** How one held interruption ended. */
export type MobileInterruptionSettlement =
  | { readonly kind: 'phone-decision'; readonly decision: 'allow' | 'reject' }
  | { readonly kind: 'phone-answer'; readonly answers: readonly MobileQuestionAnswerItem[] }
  | { readonly kind: 'delegate' }
  | { readonly kind: 'abort' }

/** Inputs the waterfall adapters extract from each Host request. */
export interface MobileInterruptionRequest {
  readonly sessionId: string | null
  readonly toolName: string | null
  readonly callId: string | null
  readonly reason: string | null
  readonly questions: readonly MobileQuestion[] | null
}

/** Handle the waterfall adapter awaits and eventually retires. */
export interface MobileInterruptionHandle {
  readonly record: MobileInterruption
  /** Resolves exactly once with how the hold ended. */
  readonly settled: Promise<MobileInterruptionSettlement>
  /** Drop the record once the underlying waterfall call settles. */
  readonly retire: () => void
}

export interface MobileInterruptionsOptions {
  /** Whether the phone page has polled the mobile API recently. */
  readonly phoneActive: () => boolean
  readonly now: () => number
  readonly log: (message: string) => void
  /** How long a live phone may keep a popup away from the desktop. */
  readonly holdMs?: number
  /** Watchdog cadence for staleness and expiry. */
  readonly tickMs?: number
}

const DEFAULT_HOLD_MS = 60_000
const DEFAULT_TICK_MS = 1_000

/** Whitelisted sizes keeping a malformed phone payload from ballooning state. */
const MAX_QUESTIONS = 8
const MAX_ANSWERS = 8
const MAX_SELECTED = 8
const MAX_LABEL_CHARS = 200
const MAX_CUSTOM_CHARS = 2_000

interface PendingEntry {
  record: MobileInterruption
  settle: (value: MobileInterruptionSettlement) => void
  settled: MobileInterruptionSettlement | null
  watcher: ReturnType<typeof setInterval> | null
}

/**
 * Registry behind the mobile interruption routes: records every ask the Host
 * waterfalls through us, resolves phone decisions against open holds, and
 * degrades to pure mirroring when no phone is around.
 */
export class MobileInterruptions {
  private readonly entries = new Map<string, PendingEntry>()
  private readonly phoneActive: () => boolean
  private readonly now: () => number
  private readonly log: (message: string) => void
  private readonly holdMs: number
  private readonly tickMs: number

  constructor(options: MobileInterruptionsOptions) {
    this.phoneActive = options.phoneActive
    this.now = options.now
    this.log = options.log
    this.holdMs = options.holdMs ?? DEFAULT_HOLD_MS
    this.tickMs = options.tickMs ?? DEFAULT_TICK_MS
  }

  /** Records visible to the phone page, oldest first. */
  snapshot(): readonly MobileInterruption[] {
    return [...this.entries.values()].map(entry => entry.record)
  }

  /**
   * Register one waterfall ask. The hold resolves with a phone claim, a
   * delegate (phone gone / expired / chose desktop), or an abort; the caller
   * maps that onto the waterfall's own return contract.
   */
  hold(request: MobileInterruptionRequest, signal?: AbortSignal): MobileInterruptionHandle {
    const record: MobileInterruption = {
      kind: request.questions === null ? 'approval' : 'question',
      key: randomUUID(),
      sessionId: request.sessionId,
      toolName: request.toolName,
      callId: request.callId,
      reason: request.reason,
      questions: request.questions,
      at: this.now(),
      delegated: false,
    }
    let settleFn!: (value: MobileInterruptionSettlement) => void
    const settled = new Promise<MobileInterruptionSettlement>(resolve => { settleFn = resolve })
    const entry: PendingEntry = {
      record,
      settle: value => {
        if (entry.settled !== null) return
        entry.settled = value
        if (value.kind === 'delegate') entry.record = { ...entry.record, delegated: true }
        if (entry.watcher !== null) clearInterval(entry.watcher)
        settleFn(value)
      },
      settled: null,
      watcher: null,
    }
    this.entries.set(record.key, entry)
    const retire = (): void => {
      if (entry.watcher !== null) clearInterval(entry.watcher)
      this.entries.delete(record.key)
    }
    if (this.phoneActive()) {
      entry.watcher = setInterval(() => {
        if (signal?.aborted === true) {
          entry.settle({ kind: 'abort' })
          return
        }
        if (!this.phoneActive() || this.now() - record.at > this.holdMs) {
          this.log(`dsh-plugin-desktop: mobile interruption hold fell through to desktop (${record.toolName ?? record.kind})`)
          entry.settle({ kind: 'delegate' })
        }
      }, this.tickMs)
      signal?.addEventListener('abort', () => { entry.settle({ kind: 'abort' }) }, { once: true })
    } else {
      // No phone around: pure mirroring, exactly the pre-existing behavior.
      entry.settle({ kind: 'delegate' })
    }
    return { record, settled, retire }
  }

  /**
   * Apply one phone decision. Returns `null` when the payload is malformed;
   * `'unknown'` when nothing is pending under that key (already answered,
   * delegated, or retired); `'ok'` when the settlement was recorded.
   */
  decide(key: string, payload: unknown): null | 'unknown' | 'ok' {
    const entry = this.entries.get(key)
    if (entry === undefined) return 'unknown'
    if (entry.settled !== null) return 'unknown'
    const body = payload as Record<string, unknown> | null
    if (body === null || typeof body !== 'object') return null
    if (entry.record.kind === 'approval') {
      const action = body.action
      if (action === 'delegate') {
        entry.settle({ kind: 'delegate' })
        return 'ok'
      }
      if (action !== 'allow' && action !== 'reject') return null
      entry.settle({ kind: 'phone-decision', decision: action })
      return 'ok'
    }
    const answers = normalizeAnswers(body.answers, entry.record.questions ?? [])
    if (answers === null) return null
    entry.settle({ kind: 'phone-answer', answers })
    return 'ok'
  }
}

/** Validate and bound one phone-submitted answer set; null when malformed. */
export function normalizeAnswers(
  value: unknown,
  questions: readonly MobileQuestion[],
): readonly MobileQuestionAnswerItem[] | null {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_ANSWERS) return null
  const byId = new Map(questions.map(question => [question.id, question]))
  const answers: MobileQuestionAnswerItem[] = []
  for (const row of value) {
    if (row === null || typeof row !== 'object' || Array.isArray(row)) return null
    const item = row as Record<string, unknown>
    if (typeof item.id !== 'string' || item.id === '') return null
    const question = byId.get(item.id)
    if (question === undefined) return null
    const labels = new Set((question.options ?? []).map(option => option.label))
    if (!Array.isArray(item.selected)) return null
    const selected: string[] = []
    for (const label of item.selected) {
      if (typeof label !== 'string' || label === '' || label.length > MAX_LABEL_CHARS) return null
      if (labels.size > 0 && !labels.has(label)) return null
      selected.push(label)
      if (selected.length > MAX_SELECTED) return null
    }
    if (!question.multiSelect && selected.length > 1) return null
    let custom: string | undefined
    if (item.custom !== undefined) {
      if (typeof item.custom !== 'string' || item.custom.length > MAX_CUSTOM_CHARS) return null
      custom = item.custom
    }
    if (selected.length === 0 && (custom === undefined || custom === '')) return null
    answers.push(custom === undefined ? { id: item.id, selected } : { id: item.id, selected, custom })
  }
  return answers
}

/** Bound one question list copied from a Host ask into phone-safe JSON. */
export function normalizeQuestions(value: unknown): readonly MobileQuestion[] | null {
  if (!Array.isArray(value) || value.length === 0 || value.length > MAX_QUESTIONS) return null
  type MutableQuestion = { -readonly [K in keyof MobileQuestion]: MobileQuestion[K] }
  type MutableOption = { -readonly [K in keyof MobileQuestionOption]: MobileQuestionOption[K] }
  const questions: MobileQuestion[] = []
  for (const row of value) {
    if (row === null || typeof row !== 'object' || Array.isArray(row)) return null
    const item = row as Record<string, unknown>
    if (typeof item.id !== 'string' || item.id === '') return null
    if (typeof item.question !== 'string' || item.question === '') return null
    const question: MutableQuestion = {
      id: item.id,
      question: clip(item.question, 2_000),
    }
    if (typeof item.detail === 'string') question.detail = clip(item.detail, 4_000)
    if (typeof item.header === 'string' && item.header !== '') question.header = clip(item.header, 64)
    if (item.multiSelect === true) question.multiSelect = true
    const intent = item.intent as Record<string, unknown> | null | undefined
    if (intent !== null && intent !== undefined && typeof intent === 'object' && intent.kind === 'plan-review' && typeof intent.approve === 'string') {
      question.intent = { kind: 'plan-review', approve: intent.approve }
    }
    if (Array.isArray(item.options)) {
      const options: MutableOption[] = []
      for (const optionRow of item.options) {
        if (options.length >= 16) break
        const option = optionRow as Record<string, unknown> | null
        if (option === null || typeof option !== 'object' || typeof option.label !== 'string' || option.label === '') continue
        const normalized: MutableOption = { label: clip(option.label, MAX_LABEL_CHARS) }
        if (typeof option.description === 'string') normalized.description = clip(option.description, 400)
        options.push(normalized)
      }
      question.options = options
    }
    questions.push(question)
  }
  return questions
}

function clip(value: string, max: number): string {
  return value.length <= max ? value : `${value.slice(0, max)}…`
}
