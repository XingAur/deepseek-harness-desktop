/**
 * The scheduler engine: due-trigger selection, per-task overlap policy,
 * cross-task FIFO queueing, session production, run bookkeeping, and
 * schedule advancement. All boundaries are injected so the module runs
 * headless under fake clocks and gateways.
 */

import { classifyMisfire, nextRuleRun } from './next-run.ts'
import {
  type SchedulerTaskFile,
  type ScheduledTask,
  type ScheduleRule,
  type TaskRun,
} from './types.ts'

/** Consecutive failed runs before the engine pauses a task automatically. */
export const PAUSE_AFTER_CONSECUTIVE_FAILURES = 3

/** Retry spacing while an external gate (quiet hours) keeps firing blocked. */
export const GATE_RETRY_MS = 60_000

/** Narrow session-control surface the engine drives; adapted from `ctx.sessionController`. */
export interface SchedulerSessionGateway {
  /** Open one session, optionally pinned to a workspace directory. */
  create(payload: { cwd?: string }): Promise<{ sessionId: string }>
  /** Submit one queued prompt; resolves after admission. */
  prompt(payload: {
    sessionId: string
    requestId: string
    mode: 'queue'
    content: unknown
  }): Promise<{ accepted: boolean }>
  /** Best-effort display rename; failures never fail the run. */
  rename?(payload: { sessionId: string; title: string }): Promise<unknown>
}

/** Terminal turn outcomes the host reports back per session. */
export type TurnOutcome = 'completed' | 'failed'

/** Host-provided boundaries around the engine core. */
export interface SchedulerEngineHooks {
  /** Session gateway used to open sessions and submit prompts. */
  gateway: SchedulerSessionGateway
  /** Load the complete durable document at start. */
  load(): Promise<SchedulerTaskFile>
  /** Persist one complete document snapshot after every mutation. */
  persist(file: SchedulerTaskFile): Promise<void>
  /** Arm one one-shot timer; the returned disposer must cancel it. */
  armTimer(delayMs: number, fire: () => void): () => void
  /** Wall clock in milliseconds; injectable for deterministic tests. */
  now(): number
  /** Structured decision log; never throws. */
  log(message: string): void
  /** Build the submitted prompt content for one run. */
  content(task: ScheduledTask): unknown
  /** Optional preparation before the task prompt (e.g. session preset commands). */
  beforePrompt?(task: ScheduledTask, sessionId: string): Promise<void> | void
  /** Optional gate consulted right before firing; false defers by {@link GATE_RETRY_MS}. */
  mayFireNow?(nowMs: number): boolean
  /** Observe terminal outcomes for notifications and history rendering. */
  onOutcome?(run: TaskRun, task: ScheduledTask): void
}

/** RFC 3339 UTC rendering used for every persisted instant. */
function toRfc3339(ms: number): string {
  return new Date(ms).toISOString()
}

function parseMs(value: string | null): number | null {
  if (value === null) return null
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : null
}

/** Next trigger instant of a rule anchored on the task's own timeline. */
export function computeNextRunMs(
  rule: ScheduleRule,
  anchorMs: number,
  nowMs: number,
): number | null {
  const next = nextRuleRun(rule, new Date(anchorMs), new Date(nowMs))
  return next === null ? null : next.getTime()
}

/** Validated editor input accepted by {@link SchedulerEngine.upsertTask}. */
export type ScheduledTaskInput = Omit<
  ScheduledTask,
  'createdAt' | 'updatedAt' | 'nextRunAt' | 'lastRunAt' | 'consecutiveFailures'
>

/**
 * Coordination state per task; the durable record lives in the document and
 * at most one run is outstanding per task at any time.
 */
interface TaskRuntime {
  task: ScheduledTask
  activeRun: TaskRun | null
}

export class SchedulerEngine {
  private readonly hooks: SchedulerEngineHooks
  private readonly runtimes = new Map<string, TaskRuntime>()
  private readonly runs = new Map<string, TaskRun[]>()
  private readonly continuedSessions = new Map<string, string>()
  /** Sessions with prompts in flight or turns outstanding, FIFO per session. */
  private readonly outstanding = new Map<string, TaskRun[]>()
  private readonly pendingTaskIds: string[] = []
  private readonly runsKeptPerTask: number
  private maxConcurrentLive: number
  private timerDispose: (() => void) | null = null
  private gateRetryPending = false
  private stopped = false
  private started = false

  constructor(hooks: SchedulerEngineHooks, maxConcurrent = 3, runsKeptPerTask = 50) {
    this.hooks = hooks
    this.maxConcurrentLive = Math.max(1, Math.min(10, Math.floor(maxConcurrent)))
    this.runsKeptPerTask = Math.max(1, runsKeptPerTask)
  }

  /** Capacity for concurrently running tasks; live-adjustable. */
  private get maxConcurrent(): number {
    return this.maxConcurrentLive
  }

  /** Load storage, recover misfires, and arm the first timer. */
  async start(misfireWindowMs: number): Promise<void> {
    if (this.started) return
    this.started = true
    const file = await this.hooks.load()
    for (const task of file.tasks) {
      this.runtimes.set(task.id, { task, activeRun: null })
      this.runs.set(task.id, file.runs[task.id] ?? [])
      this.recoverMisfire(task, misfireWindowMs)
    }
    this.scheduleTimer()
    // Recovery may have enqueued fresh misfires; release them immediately.
    this.pump()
    await this.persistSnapshot()
  }

  /** Read-only task view for host routes and surfaces. */
  listTasks(): readonly ScheduledTask[] {
    return [...this.runtimes.values()].map(entry => entry.task)
  }

  /** Apply a live max-concurrent change from the settings scope. */
  setMaxConcurrent(value: number): void {
    const next = Math.max(1, Math.min(10, Math.floor(value)))
    if (Number.isFinite(next)) this.maxConcurrentLive = next
  }

  /** Bounded run history of one task, oldest first. */
  listRuns(taskId: string): readonly TaskRun[] {
    return this.runs.get(taskId) ?? []
  }

  /** Currently active run of one task, if any. */
  activeRun(taskId: string): TaskRun | null {
    return this.runtimes.get(taskId)?.activeRun ?? null
  }

  /**
   * Insert or replace one task. Editing reschedules from the task anchor
   * again; identity and creation facts of an existing task are preserved.
   */
  async upsertTask(input: ScheduledTaskInput): Promise<ScheduledTask> {
    const nowMs = this.hooks.now()
    const existing = this.runtimes.get(input.id)
    const carried = existing?.task
    const task: ScheduledTask = carried === undefined
      ? {
        ...input,
        createdAt: toRfc3339(nowMs),
        updatedAt: toRfc3339(nowMs),
        nextRunAt: null,
        lastRunAt: null,
        consecutiveFailures: 0,
      }
      : {
        ...input,
        createdAt: carried.createdAt,
        updatedAt: toRfc3339(nowMs),
        nextRunAt: carried.nextRunAt,
        lastRunAt: carried.lastRunAt,
        consecutiveFailures: carried.consecutiveFailures,
      }
    task.nextRunAt = this.scheduleAfter(task, nowMs)
    this.runtimes.set(task.id, { task, activeRun: existing?.activeRun ?? null })
    this.scheduleTimer()
    await this.persistSnapshot()
    return task
  }

  /** Remove one task and its history; an active admission still finishes. */
  async removeTask(taskId: string): Promise<boolean> {
    if (!this.runtimes.has(taskId)) return false
    this.runtimes.delete(taskId)
    this.runs.delete(taskId)
    this.continuedSessions.delete(taskId)
    const pendingIndex = this.pendingTaskIds.indexOf(taskId)
    if (pendingIndex >= 0) this.pendingTaskIds.splice(pendingIndex, 1)
    this.scheduleTimer()
    await this.persistSnapshot()
    return true
  }

  /** Enable or disable one task; disabling keeps history and last schedule fact. */
  async setTaskEnabled(taskId: string, enabled: boolean): Promise<boolean> {
    const runtime = this.runtimes.get(taskId)
    if (runtime === undefined) return false
    runtime.task = {
      ...runtime.task,
      enabled,
      nextRunAt: enabled ? this.scheduleAfter(runtime.task, this.hooks.now()) : runtime.task.nextRunAt,
    }
    this.scheduleTimer()
    await this.persistSnapshot()
    return true
  }

  /** Trigger one task immediately, still honoring overlap and queue policy. */
  runNow(taskId: string): boolean {
    const runtime = this.runtimes.get(taskId)
    if (runtime === undefined || this.stopped) return false
    if (!runtime.task.enabled) return false
    if (runtime.activeRun !== null) {
      this.recordSkip(runtime, this.hooks.now())
      void this.persistSnapshot().catch(() => {})
      return true
    }
    this.enqueue(taskId)
    this.pump()
    return true
  }

  /**
   * Report one finished turn. The oldest outstanding run of the session
   * settles with the outcome; the queue and the schedule advance afterwards.
   */
  async completeTurn(sessionId: string, outcome: TurnOutcome): Promise<void> {
    const queue = this.outstanding.get(sessionId)
    const run = queue?.shift()
    if (queue !== undefined && queue.length === 0) this.outstanding.delete(sessionId)
    if (run === undefined) return
    const runtime = this.runtimes.get(run.taskId)
    if (runtime !== undefined && runtime.activeRun?.requestId === run.requestId) {
      runtime.activeRun = null
      runtime.task = this.applyOutcome(runtime.task, outcome)
    }
    this.finalizeRun(run, outcome === 'completed' ? 'completed' : 'failed', null)
    if (runtime !== undefined) this.hooks.onOutcome?.(run, runtime.task)
    await this.afterRunSettled(runtime)
  }

  /** Tear down: cancel outstanding runs, persist, and stop scheduling. */
  async stop(): Promise<void> {
    this.stopped = true
    this.disposeTimer()
    for (const runtime of this.runtimes.values()) {
      if (runtime.activeRun === null) continue
      this.finalizeRun(runtime.activeRun, 'cancelled', null)
      runtime.activeRun = null
    }
    this.pendingTaskIds.length = 0
    if (this.started) await this.persistSnapshot()
  }

  /** Recompute and store the next trigger instant of one enabled task. */
  private scheduleAfter(task: ScheduledTask, nowMs: number): string | null {
    if (!task.enabled) return task.nextRunAt
    const anchorMs = parseMs(task.lastRunAt) ?? parseMs(task.createdAt) ?? nowMs
    const nextMs = computeNextRunMs(task.schedule, anchorMs, nowMs)
    return nextMs === null ? null : toRfc3339(nextMs)
  }

  /** Boot-time recovery: fire fresh misfires once, expire stale ones. */
  private recoverMisfire(task: ScheduledTask, misfireWindowMs: number): void {
    if (!task.enabled) return
    const dueMs = parseMs(task.nextRunAt)
    if (dueMs === null) return
    const nowMs = this.hooks.now()
    if (dueMs > nowMs) return
    if (classifyMisfire(dueMs, nowMs, misfireWindowMs) === 'fire') {
      this.hooks.log(`scheduler: misfired trigger of "${task.name}" is inside the recovery window; firing once`)
      this.enqueue(task.id)
    } else {
      this.hooks.log(`scheduler: expired stale trigger of "${task.name}"; the schedule advances`)
      task.nextRunAt = this.scheduleAfter(task, nowMs)
    }
  }

  /** Enter one task into the FIFO unless it is already queued or active. */
  private enqueue(taskId: string): void {
    if (this.pendingTaskIds.includes(taskId)) return
    const runtime = this.runtimes.get(taskId)
    if (runtime === undefined || runtime.activeRun !== null) return
    this.pendingTaskIds.push(taskId)
  }

  /** Fire queued tasks while concurrency capacity remains. */
  private pump(): void {
    while (!this.stopped && this.pendingTaskIds.length > 0 && this.activeCount() < this.maxConcurrent) {
      const taskId = this.pendingTaskIds.shift()
      if (taskId === undefined) break
      void this.fire(taskId)
    }
  }

  private activeCount(): number {
    let count = 0
    for (const runtime of this.runtimes.values()) {
      if (runtime.activeRun !== null) count += 1
    }
    return count
  }

  /**
   * The trigger pipeline for one due task. Every early exit records the
   * reason, advances the schedule, and releases the queue.
   */
  private async fire(taskId: string): Promise<void> {
    const runtime = this.runtimes.get(taskId)
    if (runtime === undefined) return
    const nowMs = this.hooks.now()
    if (this.hooks.mayFireNow !== undefined && !this.hooks.mayFireNow(nowMs)) {
      // The task stays due (its nextRunAt remains in the past); the retry
      // timer re-collects it on the next tick, so the pump loop never spins.
      this.gateRetryPending = true
      this.hooks.log(`scheduler: firing is gated right now; "${runtime.task.name}" retries shortly`)
      return
    }
    if (runtime.activeRun !== null) {
      this.recordSkip(runtime, nowMs)
      await this.afterRunSettled(runtime)
      return
    }
    const run: TaskRun = {
      taskId,
      sessionId: null,
      requestId: `sched-${taskId}-${String(nowMs)}`,
      startedAt: toRfc3339(nowMs),
      finishedAt: null,
      status: 'running',
      error: null,
    }
    runtime.activeRun = run
    // The trigger is consumed whether or not admission succeeds; the
    // failure counter only resets when a turn completes (applyOutcome).
    runtime.task = {
      ...runtime.task,
      lastRunAt: run.startedAt,
      nextRunAt: this.scheduleAfter(runtime.task, nowMs),
    }
    this.hooks.log(`scheduler: firing "${runtime.task.name}"`)
    try {
      run.sessionId = await this.openSession(runtime.task)
    } catch (cause: unknown) {
      this.failRun(runtime, run, cause)
      await this.afterRunSettled(runtime)
      return
    }
    const queue = this.outstanding.get(run.sessionId) ?? []
    queue.push(run)
    this.outstanding.set(run.sessionId, queue)
    try {
      await this.submitPrompt(runtime.task, run)
    } catch (cause: unknown) {
      this.failRun(runtime, run, cause)
      await this.afterRunSettled(runtime)
    }
  }

  /** Produce or reuse the conversation for one run. */
  private async openSession(task: ScheduledTask): Promise<string> {
    if (task.sessionPolicy === 'continue') {
      const existing = this.continuedSessions.get(task.id)
      if (existing !== undefined) return existing
    }
    const created = await this.hooks.gateway.create(task.cwd === null ? {} : { cwd: task.cwd })
    const sessionId = created.sessionId
    if (task.sessionPolicy === 'continue') this.continuedSessions.set(task.id, sessionId)
    if (task.name !== '') {
      try {
        await this.hooks.gateway.rename?.({ sessionId, title: `⏰ ${task.name}` })
      } catch {
        // The display rename is cosmetic; admission continues on failure.
      }
    }
    return sessionId
  }

  /** Submit the task content through the queued gateway after preparation. */
  private async submitPrompt(task: ScheduledTask, run: TaskRun): Promise<void> {
    const sessionId = run.sessionId
    if (sessionId === null) throw new Error('scheduler: run has no session')
    await this.hooks.beforePrompt?.(task, sessionId)
    const accepted = await this.hooks.gateway.prompt({
      sessionId,
      requestId: run.requestId,
      mode: 'queue',
      content: this.hooks.content(task),
    })
    if (!accepted) throw new Error('scheduler: prompt admission rejected')
  }

  /** Abort an in-flight admission and settle the run as failed. */
  private failRun(runtime: TaskRuntime, run: TaskRun, cause: unknown): void {
    const message = cause instanceof Error ? cause.message : String(cause)
    this.hooks.log(`scheduler: run of "${runtime.task.name}" failed: ${message}`)
    runtime.task = this.applyOutcome(runtime.task, 'failed')
    if (runtime.activeRun?.requestId === run.requestId) runtime.activeRun = null
    if (run.sessionId !== null) {
      const queue = this.outstanding.get(run.sessionId)
      const index = queue?.findIndex(pending => pending.requestId === run.requestId) ?? -1
      if (index >= 0) queue?.splice(index, 1)
    }
    this.finalizeRun(run, 'failed', message)
    this.hooks.onOutcome?.(run, runtime.task)
  }

  /** Apply one terminal outcome to the durable task record. */
  private applyOutcome(task: ScheduledTask, outcome: TurnOutcome): ScheduledTask {
    if (outcome === 'completed') return { ...task, consecutiveFailures: 0 }
    const consecutiveFailures = task.consecutiveFailures + 1
    if (consecutiveFailures >= PAUSE_AFTER_CONSECUTIVE_FAILURES) {
      this.hooks.log(
        `scheduler: "${task.name}" failed ${String(consecutiveFailures)} times in a row; pausing until re-enabled`,
      )
      return { ...task, consecutiveFailures, enabled: false, nextRunAt: null }
    }
    return { ...task, consecutiveFailures }
  }

  /** Record one overlap skip with a durable history entry. */
  private recordSkip(runtime: TaskRuntime, nowMs: number): void {
    const run: TaskRun = {
      taskId: runtime.task.id,
      sessionId: null,
      requestId: `sched-skip-${runtime.task.id}-${String(nowMs)}`,
      startedAt: toRfc3339(nowMs),
      finishedAt: toRfc3339(nowMs),
      status: 'skipped-overlap',
      error: null,
    }
    this.appendRun(run)
    this.hooks.log(`scheduler: skipped "${runtime.task.name}"; the previous run is still active`)
  }

  /** Append one terminal run to the bounded history of its task. */
  private finalizeRun(run: TaskRun, status: Exclude<TaskRun['status'], 'running'>, error: string | null): void {
    run.status = status
    run.error = error
    run.finishedAt = toRfc3339(this.hooks.now())
    this.appendRun(run)
  }

  private appendRun(run: TaskRun): void {
    if (!this.runtimes.has(run.taskId)) return
    const history = this.runs.get(run.taskId) ?? []
    history.push(run)
    if (history.length > this.runsKeptPerTask) history.splice(0, history.length - this.runsKeptPerTask)
    this.runs.set(run.taskId, history)
  }

  /** Advance the schedule, persist, release the queue, and rearm the timer. */
  private async afterRunSettled(runtime: TaskRuntime | undefined): Promise<void> {
    if (runtime !== undefined && runtime.activeRun === null) {
      runtime.task = {
        ...runtime.task,
        nextRunAt: this.scheduleAfter(runtime.task, this.hooks.now()),
      }
    }
    this.persistSnapshot().catch(cause => {
      this.hooks.log(`scheduler: persist failed: ${cause instanceof Error ? cause.message : String(cause)}`)
    })
    await Promise.resolve()
    this.scheduleTimer()
    this.pump()
  }

  /** Arm exactly one timer for the earliest due trigger or the next gate retry. */
  private scheduleTimer(retryMs?: number): void {
    this.disposeTimer()
    if (this.stopped) return
    let dueInMs: number | null
    if (retryMs !== undefined) {
      dueInMs = retryMs
    } else if (this.gateRetryPending) {
      dueInMs = GATE_RETRY_MS
    } else {
      let earliestMs: number | null = null
      for (const runtime of this.runtimes.values()) {
        if (!runtime.task.enabled) continue
        const nextMs = parseMs(runtime.task.nextRunAt)
        if (nextMs !== null && (earliestMs === null || nextMs < earliestMs)) earliestMs = nextMs
      }
      dueInMs = earliestMs === null ? null : Math.max(0, earliestMs - this.hooks.now())
    }
    if (dueInMs === null) return
    this.timerDispose = this.hooks.armTimer(dueInMs, () => {
      void this.onTimer()
    })
  }

  /** Fire, skip, or re-gate every due task, then rearm. */
  private async onTimer(): Promise<void> {
    if (this.stopped) return
    const nowMs = this.hooks.now()
    this.gateRetryPending = false
    let skipped = false
    for (const runtime of this.runtimes.values()) {
      if (!runtime.task.enabled) continue
      const dueMs = parseMs(runtime.task.nextRunAt)
      if (dueMs === null || dueMs > nowMs) continue
      if (runtime.activeRun !== null) {
        // Overlap policy: the trigger is consumed as a durable skip.
        this.recordSkip(runtime, nowMs)
        runtime.task = {
          ...runtime.task,
          nextRunAt: this.scheduleAfter(runtime.task, nowMs),
        }
        skipped = true
        continue
      }
      this.enqueue(runtime.task.id)
    }
    this.pump()
    this.scheduleTimer()
    if (skipped) {
      this.persistSnapshot().catch(cause => {
        this.hooks.log(`scheduler: persist failed: ${cause instanceof Error ? cause.message : String(cause)}`)
      })
    }
  }

  private disposeTimer(): void {
    if (this.timerDispose === null) return
    this.timerDispose()
    this.timerDispose = null
  }

  /** Snapshot the whole engine state for one durable write. */
  private persistSnapshot(): Promise<void> {
    const tasks = [...this.runtimes.values()].map(entry => entry.task)
    const runs: Record<string, TaskRun[]> = {}
    for (const [taskId, history] of this.runs.entries()) runs[taskId] = [...history]
    return this.hooks.persist({ version: 1, tasks, runs })
  }
}
