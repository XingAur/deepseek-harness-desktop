/** Schedule arithmetic: cron validation, next-run computation, and preview. */

import { Cron } from 'croner'
import { MAX_EVERY_SECONDS, MIN_EVERY_SECONDS, type ScheduleRule } from './types.ts'

/** Outcome of validating one stored or user-entered cron expression. */
export type CronValidation =
  | { readonly ok: true }
  | { readonly ok: false; readonly error: string }

function cronFieldCount(expression: string): number {
  return expression.trim().split(/\s+/u).length
}

/**
 * Validate one cron expression. Accepts the standard five-field form and
 * croner's six-field seconds extension; everything else, including empty
 * text and unsupported nicknames, is rejected so stored rules stay uniform.
 */
export function validateCronExpression(expression: string): CronValidation {
  const trimmed = expression.trim()
  if (trimmed === '') return { ok: false, error: 'empty expression' }
  if (trimmed.length > 256) return { ok: false, error: 'expression too long' }
  if (trimmed.startsWith('@')) return { ok: false, error: 'cron nicknames are not accepted' }
  const fields = cronFieldCount(trimmed)
  if (fields !== 5 && fields !== 6) {
    return { ok: false, error: `expected 5 or 6 fields, found ${String(fields)}` }
  }
  try {
    const cron = new Cron(trimmed)
    if (cron.nextRun() === null) return { ok: false, error: 'expression never fires' }
    return { ok: true }
  } catch (cause: unknown) {
    return {
      ok: false,
      error: cause instanceof Error ? cause.message : 'invalid cron expression',
    }
  }
}

/** Validate one schedule rule of either kind. */
export function validateScheduleRule(rule: ScheduleRule): CronValidation {
  if (rule.kind === 'every') {
    return Number.isSafeInteger(rule.seconds)
      && rule.seconds >= MIN_EVERY_SECONDS && rule.seconds <= MAX_EVERY_SECONDS
      ? { ok: true }
      : { ok: false, error: `fixed interval must be between ${String(MIN_EVERY_SECONDS)} and ${String(MAX_EVERY_SECONDS)} seconds` }
  }
  return validateCronExpression(rule.expression)
}

/**
 * Compute the earliest cron trigger strictly after `after`, or `null` when
 * the expression never fires again. Cron is evaluated in the system-local
 * timezone, matching every user-facing rendering.
 */
export function nextCronRun(expression: string, after: Date): Date | null {
  const validation = validateCronExpression(expression)
  if (!validation.ok) return null
  try {
    return new Cron(expression.trim()).nextRun(after)
  } catch {
    return null
  }
}

/**
 * Anchor for a fixed-interval rule: one interval after `anchor`, advanced
 * forward while still not in the future relative to `after`. A task that was
 * paused for days therefore resumes on its own spacing instead of bursting.
 */
export function nextIntervalRun(seconds: number, anchor: Date, after: Date): Date {
  const spacingMs = seconds * 1000
  let candidate = new Date(anchor.getTime() + spacingMs)
  if (spacingMs <= 0) return candidate
  while (candidate.getTime() <= after.getTime()) {
    candidate = new Date(candidate.getTime() + spacingMs)
  }
  return candidate
}

/** Compute the next trigger of any rule strictly after `after`. */
export function nextRuleRun(rule: ScheduleRule, anchor: Date, after: Date): Date | null {
  return rule.kind === 'every'
    ? nextIntervalRun(rule.seconds, anchor, after)
    : nextCronRun(rule.expression, after)
}

/** Render up to `count` upcoming triggers for editor preview, earliest first. */
export function previewRuleRuns(rule: ScheduleRule, count: number, from: Date): Date[] {
  if (count <= 0) return []
  if (!validateScheduleRule(rule).ok) return []
  if (rule.kind === 'cron') {
    const validation = validateCronExpression(rule.expression)
    if (!validation.ok) return []
    try {
      return new Cron(rule.expression.trim()).nextRuns(count, from)
    } catch {
      return []
    }
  }
  const runs: Date[] = []
  let cursor = from
  for (let index = 0; index < count; index += 1) {
    const next = nextIntervalRun(rule.seconds, cursor, cursor)
    runs.push(next)
    cursor = next
  }
  return runs
}

/** Misfire verdict for one persisted trigger that came due while away. */
export type MisfireVerdict = 'fire' | 'expire'

/**
 * Classify one overdue trigger. Within the recovery window the scheduler
 * still fires it once; anything older is expired and the schedule advances.
 */
export function classifyMisfire(dueMs: number, nowMs: number, windowMs: number): MisfireVerdict {
  return nowMs - dueMs <= windowMs ? 'fire' : 'expire'
}
