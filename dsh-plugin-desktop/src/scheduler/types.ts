/** Durable scheduler task vocabulary shared by the Host row and the native editor. */

/** Minimum fixed-interval spacing accepted from the editor, storage, and the runtime. */
export const MIN_EVERY_SECONDS = 60

/** Maximum fixed-interval spacing: one leap year, keeping timer math in safe range. */
export const MAX_EVERY_SECONDS = 366 * 86_400

/** How a task produces its conversation at each trigger. */
export type SchedulerSessionPolicy = 'fresh' | 'continue'

/**
 * Authoritative schedule rules. Fixed spacing anchors on the task's own
 * timeline; the cron form is the standard five- or six-field expression
 * evaluated in the machine's system-local timezone.
 */
export type ScheduleRule =
  | { kind: 'every'; seconds: number }
  | { kind: 'cron'; expression: string }

/** Conflict policy reserved for future values; version 1 always skips. */
export type SchedulerOverlapPolicy = 'skip'

/** One durable scheduled task owned by the Desktop scheduler. */
export interface ScheduledTask {
  /** Stable unique identity; never reused. */
  id: string
  /** Human-facing display name. */
  name: string
  /** Complete prompt content submitted at each trigger. */
  prompt: string
  /** Workspace directory opened for the session; `null` uses the default. */
  cwd: string | null
  /** Authoritative schedule rule. */
  schedule: ScheduleRule
  /** Whether the scheduler may fire this task at all. */
  enabled: boolean
  /** Whether each trigger opens a fresh session or continues one running thread. */
  sessionPolicy: SchedulerSessionPolicy
  /** Session permission preset applied at trigger time; `null` keeps the profile default. */
  permissionPreset: string | null
  /** What happens when the previous run of this task is still active. */
  overlap: SchedulerOverlapPolicy
  /** Whether completions and failures raise a native notification. */
  notify: boolean
  /** RFC 3339 UTC creation instant. */
  createdAt: string
  /** RFC 3339 UTC last-edit instant. */
  updatedAt: string
  /** RFC 3339 UTC next trigger persisted for restart recovery; `null` when disabled or exhausted. */
  nextRunAt: string | null
  /** RFC 3339 UTC instant the task last fired, of any outcome. */
  lastRunAt: string | null
  /** Consecutive failed runs; reaches the pause threshold only through failures. */
  consecutiveFailures: number
}

/** Terminal and transient run outcomes recorded per task. */
export type TaskRunStatus =
  | 'running'
  | 'completed'
  | 'failed'
  | 'skipped-overlap'
  | 'cancelled'

/** One recorded trigger attempt. */
export interface TaskRun {
  /** Owning task id. */
  taskId: string
  /** Session opened for this run; `null` when admission never created one. */
  sessionId: string | null
  /** Prompt correlation id echoed by the durable user message. */
  requestId: string
  /** RFC 3339 UTC admission instant. */
  startedAt: string
  /** RFC 3339 UTC terminal instant; `null` while running. */
  finishedAt: string | null
  /** Recorded outcome. */
  status: TaskRunStatus
  /** Failure detail; `null` unless the run failed. */
  error: string | null
}

/** Durable on-disk document shape; version 1. */
export interface SchedulerTaskFile {
  version: 1
  tasks: ScheduledTask[]
  /** Bounded per-task run history keyed by task id, oldest first. */
  runs: Record<string, TaskRun[]>
}

/** Empty version-1 document used by recovery and first run. */
export function emptySchedulerTaskFile(): SchedulerTaskFile {
  return { version: 1, tasks: [], runs: {} }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isRfc3339(value: unknown): value is string {
  return typeof value === 'string'
    && Number.isFinite(Date.parse(value))
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/u.test(value)
}

function optionalRfc3339(value: unknown): string | null {
  if (value === null || value === undefined) return null
  return isRfc3339(value) ? value : null
}

/** Normalize one unknown schedule rule; returns `null` when the value is not a rule. */
export function normalizeScheduleRule(value: unknown): ScheduleRule | null {
  if (!isRecord(value)) return null
  if (value.kind === 'every') {
    const seconds = value.seconds
    return typeof seconds === 'number' && Number.isSafeInteger(seconds)
      && seconds >= MIN_EVERY_SECONDS && seconds <= MAX_EVERY_SECONDS
      ? { kind: 'every', seconds }
      : null
  }
  if (value.kind === 'cron') {
    return typeof value.expression === 'string'
      && value.expression.trim() !== '' && value.expression.length <= 256
      ? { kind: 'cron', expression: value.expression.trim() }
      : null
  }
  return null
}

/** Session-policy whitelist applied to stored values. */
export function normalizeSessionPolicy(value: unknown): SchedulerSessionPolicy {
  return value === 'continue' ? 'continue' : 'fresh'
}

function normalizeTrimmedText(value: unknown, maxLength: number): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed !== '' && trimmed.length <= maxLength ? trimmed : null
}

/**
 * Normalize one unknown stored task. Invalid tasks return `null` and the
 * store drops them individually instead of rejecting the whole file.
 */
export function normalizeScheduledTask(value: unknown): ScheduledTask | null {
  if (!isRecord(value)) return null
  const id = normalizeTrimmedText(value.id, 128)
  const name = normalizeTrimmedText(value.name, 200)
  const prompt = typeof value.prompt === 'string'
    && value.prompt.trim() !== '' && value.prompt.length <= 100_000
    ? value.prompt
    : null
  const schedule = normalizeScheduleRule(value.schedule)
  const createdAt = isRfc3339(value.createdAt) ? value.createdAt : null
  if (id === null || name === null || prompt === null || schedule === null || createdAt === null) {
    return null
  }
  const cwd = typeof value.cwd === 'string' && value.cwd.trim() !== '' && value.cwd.length <= 4096
    ? value.cwd
    : null
  const permissionPreset = normalizeTrimmedText(value.permissionPreset, 200)
  const updatedAt = optionalRfc3339(value.updatedAt) ?? createdAt
  const nextRunAt = optionalRfc3339(value.nextRunAt)
  const lastRunAt = optionalRfc3339(value.lastRunAt)
  const consecutiveFailures = typeof value.consecutiveFailures === 'number'
    && Number.isSafeInteger(value.consecutiveFailures) && value.consecutiveFailures >= 0
    ? value.consecutiveFailures
    : 0
  return {
    id,
    name,
    prompt,
    cwd,
    schedule,
    enabled: value.enabled !== false,
    sessionPolicy: normalizeSessionPolicy(value.sessionPolicy),
    permissionPreset,
    overlap: 'skip',
    notify: value.notify !== false,
    createdAt,
    updatedAt,
    nextRunAt,
    lastRunAt,
    consecutiveFailures,
  }
}

function normalizeTaskRun(value: unknown): TaskRun | null {
  if (!isRecord(value)) return null
  const taskId = normalizeTrimmedText(value.taskId, 128)
  const requestId = normalizeTrimmedText(value.requestId, 128)
  const startedAt = isRfc3339(value.startedAt) ? value.startedAt : null
  const status = typeof value.status === 'string'
    && (value.status === 'running' || value.status === 'completed' || value.status === 'failed'
      || value.status === 'skipped-overlap' || value.status === 'cancelled')
    ? value.status
    : null
  if (taskId === null || requestId === null || startedAt === null || status === null) return null
  const sessionId = normalizeTrimmedText(value.sessionId, 128)
  const finishedAt = optionalRfc3339(value.finishedAt)
  const error = typeof value.error === 'string' && value.error.length <= 2000 ? value.error : null
  return { taskId, sessionId, requestId, startedAt, finishedAt, status, error }
}

/** Keep only the newest `keep` runs of one task, preserving oldest-first order. */
export function truncateTaskRuns(runs: readonly TaskRun[], keep: number): TaskRun[] {
  return runs.slice(Math.max(0, runs.length - keep))
}

/**
 * Normalize one unknown stored document. Unreadable shapes fall back to the
 * empty version-1 file; every task and run entry is validated separately so
 * one corrupt entry never discards the rest.
 */
export function normalizeSchedulerTaskFile(value: unknown, runsKeptPerTask: number): SchedulerTaskFile {
  if (!isRecord(value)) return emptySchedulerTaskFile()
  const rawTasks = Array.isArray(value.tasks) ? value.tasks : []
  const tasks = rawTasks
    .map(task => normalizeScheduledTask(task))
    .filter((task): task is ScheduledTask => task !== null)
  const rawRuns = isRecord(value.runs) ? value.runs : {}
  const runs: Record<string, TaskRun[]> = {}
  for (const [taskId, entries] of Object.entries(rawRuns)) {
    if (!Array.isArray(entries) || tasks.every(task => task.id !== taskId)) continue
    const normalized = entries
      .map(entry => normalizeTaskRun(entry))
      .filter((entry): entry is TaskRun => entry !== null)
    const truncated = truncateTaskRuns(normalized, runsKeptPerTask)
    if (truncated.length > 0) runs[taskId] = truncated
  }
  return { version: 1, tasks, runs }
}
