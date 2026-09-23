/** Quiet-hours gate: one local-time window during which firing defers. */

/** Parse one `HH:MM` local-time-of-day value into minutes; `null` when invalid. */
export function parseTimeOfDay(value: string): number | null {
  const match = /^(\d{1,2}):(\d{2})$/u.exec(value.trim())
  if (match === null) return null
  const hours = Number(match[1])
  const minutes = Number(match[2])
  if (!Number.isInteger(hours) || !Number.isInteger(minutes)) return null
  if (hours < 0 || hours > 23 || minutes < 0 || minutes > 59) return null
  return hours * 60 + minutes
}

/** Minutes since local midnight for one wall-clock instant. */
export function localMinuteOfDay(nowMs: number): number {
  const now = new Date(nowMs)
  return now.getHours() * 60 + now.getMinutes()
}

/**
 * Whether `nowMs` falls inside one quiet-hours window. A window that crosses
 * midnight (start greater than end) spans from start through the next
 * midnight to end; an invalid window never quiets anything.
 */
export function isQuietHours(
  quietStart: string,
  quietEnd: string,
  nowMs: number,
): boolean {
  const start = parseTimeOfDay(quietStart)
  const end = parseTimeOfDay(quietEnd)
  if (start === null || end === null) return false
  const minute = localMinuteOfDay(nowMs)
  if (start === end) return false
  if (start < end) return minute >= start && minute < end
  return minute >= start || minute < end
}
