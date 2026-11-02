import { describe, expect, it } from 'vitest'
import {
  classifyMisfire,
  nextCronRun,
  nextIntervalRun,
  nextRuleRun,
  previewRuleRuns,
  validateCronExpression,
  validateScheduleRule,
} from '../src/scheduler/next-run.ts'
import { MAX_EVERY_SECONDS, MIN_EVERY_SECONDS } from '../src/scheduler/types.ts'

/** Local-time constructor keeps expectations independent of the test machine timezone. */
function local(year: number, month: number, day: number, hour: number, minute: number): Date {
  return new Date(year, month - 1, day, hour, minute, 0, 0)
}

describe('cron expression validation', () => {
  it('accepts the standard five-field form', () => {
    expect(validateCronExpression('30 9 * * 1-5')).toEqual({ ok: true })
    expect(validateCronExpression('0 0 1 * *')).toEqual({ ok: true })
    expect(validateCronExpression('*/15 * * * *')).toEqual({ ok: true })
  })

  it('accepts croner six-field seconds extension', () => {
    expect(validateCronExpression('0 30 9 * * 1-5')).toEqual({ ok: true })
  })

  it('rejects empty and oversized expressions', () => {
    expect(validateCronExpression('  ').ok).toBe(false)
    expect(validateCronExpression(`${'* '.repeat(130)}*`).ok).toBe(false)
  })

  it('rejects field counts outside five and six', () => {
    expect(validateCronExpression('30 9 * *').ok).toBe(false)
    expect(validateCronExpression('0 30 9 * * * 2026').ok).toBe(false)
  })

  it('rejects nicknames so stored rules stay uniform', () => {
    expect(validateCronExpression('@daily')).toEqual({ ok: false, error: 'cron nicknames are not accepted' })
    expect(validateCronExpression('@hourly').ok).toBe(false)
  })

  it('rejects garbage with the parser message', () => {
    const result = validateCronExpression('garbage * * * *')
    expect(result.ok).toBe(false)
  })

  it('validates fixed-interval rules against the spacing bounds', () => {
    expect(validateScheduleRule({ kind: 'every', seconds: MIN_EVERY_SECONDS })).toEqual({ ok: true })
    expect(validateScheduleRule({ kind: 'every', seconds: MAX_EVERY_SECONDS })).toEqual({ ok: true })
    expect(validateScheduleRule({ kind: 'every', seconds: 59 }).ok).toBe(false)
    expect(validateScheduleRule({ kind: 'every', seconds: MAX_EVERY_SECONDS + 1 }).ok).toBe(false)
    expect(validateScheduleRule({ kind: 'every', seconds: Number.MAX_SAFE_INTEGER }).ok).toBe(false)
    expect(validateScheduleRule({ kind: 'cron', expression: '0 9 * * *' })).toEqual({ ok: true })
    expect(validateScheduleRule({ kind: 'cron', expression: 'nope' }).ok).toBe(false)
  })
})

describe('cron next-run computation', () => {
  it('returns the next weekday occurrence strictly after the anchor', () => {
    // Monday 2026-01-05 09:30 local matches the pattern; the next run is Tuesday.
    const after = local(2026, 1, 5, 9, 30)
    expect(nextCronRun('30 9 * * 1-5', after)).toEqual(local(2026, 1, 6, 9, 30))
  })

  it('skips weekend days for weekday-only patterns', () => {
    // Friday 2026-01-09 → Monday 2026-01-12.
    const after = local(2026, 1, 9, 9, 30)
    expect(nextCronRun('30 9 * * 1-5', after)).toEqual(local(2026, 1, 12, 9, 30))
  })

  it('returns the first match after an arbitrary earlier anchor', () => {
    expect(nextCronRun('0 0 1 * *', local(2026, 1, 2, 0, 0))).toEqual(local(2026, 2, 1, 0, 0))
  })

  it('returns null for invalid expressions instead of throwing', () => {
    expect(nextCronRun('garbage * * * *', local(2026, 1, 1, 0, 0))).toBeNull()
    expect(nextCronRun('@daily', local(2026, 1, 1, 0, 0))).toBeNull()
  })
})

describe('fixed-interval arithmetic', () => {
  it('anchors one spacing after the task anchor', () => {
    const anchor = local(2026, 1, 1, 8, 0)
    expect(nextIntervalRun(3600, anchor, local(2026, 1, 1, 8, 30))).toEqual(local(2026, 1, 1, 9, 0))
  })

  it('advances whole spacings until the result is strictly future', () => {
    const anchor = local(2026, 1, 1, 8, 0)
    // Ten hours later the next multiple of the one-hour spacing is 19:00.
    expect(nextIntervalRun(3600, anchor, local(2026, 1, 1, 18, 30))).toEqual(local(2026, 1, 1, 19, 0))
  })

  it('never returns the boundary instant itself', () => {
    const anchor = local(2026, 1, 1, 8, 0)
    const due = new Date(anchor.getTime() + 3600 * 1000)
    expect(nextIntervalRun(3600, anchor, due).getTime()).toBeGreaterThan(due.getTime())
  })
})

describe('unified rule dispatch', () => {
  it('routes every-rule and cron-rule computation', () => {
    const anchor = local(2026, 3, 10, 7, 0)
    expect(nextRuleRun({ kind: 'every', seconds: 120 }, anchor, anchor))
      .toEqual(new Date(anchor.getTime() + 120_000))
    expect(nextRuleRun({ kind: 'cron', expression: '0 9 * * *' }, anchor, anchor))
      .toEqual(local(2026, 3, 10, 9, 0))
  })
})

describe('editor preview', () => {
  it('renders the requested number of upcoming triggers in order', () => {
    const runs = previewRuleRuns({ kind: 'cron', expression: '0 9 * * *' }, 3, local(2026, 6, 1, 10, 0))
    expect(runs).toHaveLength(3)
    expect(runs[0]).toEqual(local(2026, 6, 2, 9, 0))
    expect(runs[1]).toEqual(local(2026, 6, 3, 9, 0))
    expect(runs[2]).toEqual(local(2026, 6, 4, 9, 0))
  })

  it('chains fixed-interval previews from the requested instant', () => {
    const from = local(2026, 6, 1, 10, 0)
    const runs = previewRuleRuns({ kind: 'every', seconds: 1800 }, 3, from)
    expect(runs[0]).toEqual(new Date(from.getTime() + 1800_000))
    expect(runs[2]).toEqual(new Date(from.getTime() + 3 * 1800_000))
  })

  it('returns nothing for invalid rules', () => {
    expect(previewRuleRuns({ kind: 'cron', expression: 'bad rule here' }, 3, new Date())).toEqual([])
    expect(previewRuleRuns({ kind: 'every', seconds: 10 }, 3, new Date())).toEqual([])
    expect(previewRuleRuns({ kind: 'cron', expression: '0 9 * * *' }, 0, new Date())).toEqual([])
  })
})

describe('misfire classification', () => {
  const windowMs = 15 * 60_000

  it('fires overdue triggers inside the recovery window', () => {
    const due = Date.now() - windowMs
    expect(classifyMisfire(due, Date.now(), windowMs)).toBe('fire')
    expect(classifyMisfire(due, Date.now(), windowMs - 1)).toBe('expire')
  })

  it('expires triggers older than the window and keeps fresh ones', () => {
    const now = 1_000_000
    expect(classifyMisfire(now - 1, now, windowMs)).toBe('fire')
    expect(classifyMisfire(now, now, windowMs)).toBe('fire')
    expect(classifyMisfire(now - windowMs - 1, now, windowMs)).toBe('expire')
  })
})
