import { describe, expect, it } from 'vitest'
import { schedulerNotice, scheduledPromptContent } from '../src/scheduler/copy.ts'
import { isQuietHours, localMinuteOfDay, parseTimeOfDay } from '../src/scheduler/quiet-hours.ts'

/** Local wall-clock instant for TZ-independent quiet-hours assertions. */
function local(y: number, m: number, d: number, h: number, min: number): number {
  return new Date(y, m - 1, d, h, min, 0, 0).getTime()
}

describe('parseTimeOfDay', () => {
  it('accepts zero-padded and single-digit hours', () => {
    expect(parseTimeOfDay('23:00')).toBe(23 * 60)
    expect(parseTimeOfDay('7:05')).toBe(7 * 60 + 5)
    expect(parseTimeOfDay(' 00:59 ')).toBe(59)
  })

  it('rejects malformed or out-of-range values', () => {
    expect(parseTimeOfDay('24:00')).toBeNull()
    expect(parseTimeOfDay('12:60')).toBeNull()
    expect(parseTimeOfDay('noon')).toBeNull()
    expect(parseTimeOfDay('')).toBeNull()
    expect(parseTimeOfDay('-1:00')).toBeNull()
  })
})

describe('localMinuteOfDay', () => {
  it('returns minutes since local midnight', () => {
    expect(localMinuteOfDay(local(2026, 1, 5, 9, 7))).toBe(9 * 60 + 7)
    expect(localMinuteOfDay(local(2026, 1, 5, 0, 0))).toBe(0)
    expect(localMinuteOfDay(local(2026, 1, 5, 23, 59))).toBe(23 * 60 + 59)
  })
})

describe('isQuietHours', () => {
  it('handles a same-day window', () => {
    expect(isQuietHours('13:00', '15:00', local(2026, 1, 5, 14, 0))).toBe(true)
    expect(isQuietHours('13:00', '15:00', local(2026, 1, 5, 12, 59))).toBe(false)
    expect(isQuietHours('13:00', '15:00', local(2026, 1, 5, 15, 0))).toBe(false)
  })

  it('handles a window that crosses midnight', () => {
    expect(isQuietHours('23:00', '07:00', local(2026, 1, 5, 23, 30))).toBe(true)
    expect(isQuietHours('23:00', '07:00', local(2026, 1, 6, 3, 0))).toBe(true)
    expect(isQuietHours('23:00', '07:00', local(2026, 1, 6, 6, 59))).toBe(true)
    expect(isQuietHours('23:00', '07:00', local(2026, 1, 6, 7, 0))).toBe(false)
    expect(isQuietHours('23:00', '07:00', local(2026, 1, 6, 12, 0))).toBe(false)
  })

  it('treats invalid or empty windows as never quiet', () => {
    expect(isQuietHours('25:00', '07:00', local(2026, 1, 5, 3, 0))).toBe(false)
    expect(isQuietHours('23:00', 'nope', local(2026, 1, 5, 3, 0))).toBe(false)
    expect(isQuietHours('23:00', '23:00', local(2026, 1, 5, 23, 30))).toBe(false)
  })
})

describe('scheduler copy', () => {
  it('renders the prompt header in both locales', () => {
    const zh = scheduledPromptContent('zh', '日报', '2026-01-05 09:00', '汇总仓库')
    expect(zh).toContain('【定时任务】日报')
    expect(zh).toContain('汇总仓库')
    const en = scheduledPromptContent('en', 'Report', '2026-01-05 09:00', 'Summarize')
    expect(en).toContain('[Scheduled task] Report')
    expect(en).toContain('Summarize')
  })

  it('renders one notification per outcome kind per locale', () => {
    for (const kind of ['task-completed', 'task-failed', 'task-paused', 'task-skipped'] as const) {
      for (const locale of ['zh', 'en'] as const) {
        const notice = schedulerNotice(locale, kind, '日报')
        expect(notice.title.length).toBeGreaterThan(0)
        expect(notice.body).toContain('日报')
      }
    }
  })
})
