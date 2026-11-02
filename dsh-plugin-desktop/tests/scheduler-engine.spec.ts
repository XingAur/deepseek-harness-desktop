import { describe, expect, it, vi } from 'vitest'
import {
  GATE_RETRY_MS,
  PAUSE_AFTER_CONSECUTIVE_FAILURES,
  SchedulerEngine,
  type SchedulerEngineHooks,
  type SchedulerSessionGateway,
  type TurnOutcome,
} from '../src/scheduler/engine.ts'
import { emptySchedulerTaskFile, type SchedulerTaskFile, type ScheduledTask } from '../src/scheduler/types.ts'

/** Drain the engine's fire-and-forget promise chains deterministically. */
async function settle(): Promise<void> {
  for (let index = 0; index < 8; index += 1) await Promise.resolve()
}

interface TimerArmed {
  delayMs: number
  fire: () => void
  disposed: boolean
}

function taskInput(overrides: Partial<ScheduledTask> = {}): Omit<
  ScheduledTask,
  'createdAt' | 'updatedAt' | 'nextRunAt' | 'lastRunAt' | 'consecutiveFailures'
> {
  return {
    id: 'task-1',
    name: 'Daily report',
    prompt: 'Summarize the working tree.',
    cwd: null,
    schedule: { kind: 'every', seconds: 3600 },
    enabled: true,
    sessionPolicy: 'fresh',
    permissionPreset: null,
    overlap: 'skip',
    notify: true,
    ...overrides,
  }
}

function harness(overrides: {
  gateway?: Partial<SchedulerSessionGateway>
  maxConcurrent?: number
  storage?: SchedulerTaskFile
} = {}): {
  engine: SchedulerEngine
  hooks: SchedulerEngineHooks
  nowMs: number
  timers: TimerArmed[]
  logs: string[]
  outcomes: Array<{ status: string; taskName: string }>
  persistCalls: number
  create: ReturnType<typeof vi.fn>
  prompt: ReturnType<typeof vi.fn>
  rename: ReturnType<typeof vi.fn>
  advanceClock(ms: number): void
  fireTimer(): Promise<void>
  settle(): Promise<void>
} {
  const state = {
    nowMs: 1_770_000_000_000,
    timers: [] as TimerArmed[],
    logs: [] as string[],
    outcomes: [] as Array<{ status: string; taskName: string }>,
    persistCalls: 0,
  }
  const create = vi.fn(async (_payload: { cwd?: string }) => {
    return { sessionId: `session-${String(create.mock.calls.length)}` }
  })
  const prompt = vi.fn(async () => ({ accepted: true }))
  const rename = vi.fn(async () => ({}))
  const gateway: SchedulerSessionGateway = {
    create: overrides.gateway?.create ?? create,
    prompt: overrides.gateway?.prompt ?? prompt,
    rename: overrides.gateway?.rename ?? rename,
  }
  const hooks: SchedulerEngineHooks = {
    gateway,
    load: vi.fn(async () => overrides.storage ?? emptySchedulerTaskFile()),
    persist: vi.fn(async (file: SchedulerTaskFile) => {
      state.persistCalls += 1
      void file
    }),
    armTimer: (delayMs: number, fire: () => void) => {
      const armed: TimerArmed = { delayMs, fire, disposed: false }
      state.timers.push(armed)
      return () => {
        armed.disposed = true
      }
    },
    now: () => state.nowMs,
    log: (message: string) => {
      state.logs.push(message)
    },
    content: (task: ScheduledTask) => `run ${task.name}: ${task.prompt}`,
    onOutcome: (run, task) => {
      state.outcomes.push({ status: run.status, taskName: task.name })
    },
  }
  const engine = new SchedulerEngine(hooks, overrides.maxConcurrent ?? 3)
  return {
    engine,
    hooks,
    get nowMs() {
      return state.nowMs
    },
    set nowMs(value: number) {
      state.nowMs = value
    },
    get timers() {
      return state.timers
    },
    get logs() {
      return state.logs
    },
    get outcomes() {
      return state.outcomes
    },
    get persistCalls() {
      return state.persistCalls
    },
    create,
    prompt,
    rename,
    advanceClock(ms: number) {
      state.nowMs += ms
    },
    async fireTimer() {
      const timer = state.timers.at(-1)
      if (timer === undefined) throw new Error('no timer armed')
      timer.disposed = true
      // Model the armed delay elapsing before the callback runs.
      state.nowMs += timer.delayMs
      timer.fire()
      await settle()
    },
    settle,
  }
}

describe('scheduler engine lifecycle', () => {
  it('starts from empty storage without arming a timer', async () => {
    const app = harness()
    await app.engine.start(15 * 60_000)
    expect(app.engine.listTasks()).toEqual([])
    expect(app.timers).toEqual([])
    expect(app.persistCalls).toBeGreaterThan(0)
  })

  it('arms one timer for the computed next trigger', async () => {
    const app = harness()
    await app.engine.start(15 * 60_000)
    await app.engine.upsertTask(taskInput())
    const tasks = app.engine.listTasks()
    expect(tasks).toHaveLength(1)
    expect(tasks[0]?.nextRunAt).not.toBeNull()
    expect(app.timers).toHaveLength(1)
    expect(app.timers[0]?.delayMs).toBe(3600_000)
  })

  it('replaces an existing task while carrying identity and history facts', async () => {
    const app = harness()
    await app.engine.start(15 * 60_000)
    const first = await app.engine.upsertTask(taskInput())
    const second = await app.engine.upsertTask(taskInput({ id: first.id, name: 'Renamed' }))
    expect(second.createdAt).toBe(first.createdAt)
    expect(second.name).toBe('Renamed')
    expect(app.engine.listTasks()).toHaveLength(1)
  })

  it('removes tasks and stops tracking their history', async () => {
    const app = harness()
    await app.engine.start(15 * 60_000)
    await app.engine.upsertTask(taskInput())
    expect(await app.engine.removeTask('task-1')).toBe(true)
    expect(await app.engine.removeTask('task-1')).toBe(false)
    expect(app.engine.listTasks()).toEqual([])
  })
})

describe('trigger pipeline', () => {
  it('opens a session, renames it, and submits the task content', async () => {
    const app = harness()
    await app.engine.start(15 * 60_000)
    await app.engine.upsertTask(taskInput({ cwd: '/tmp/workspace' }))
    await app.fireTimer()
    expect(app.create).toHaveBeenCalledWith({ cwd: '/tmp/workspace' })
    expect(app.rename).toHaveBeenCalledWith({ sessionId: 'session-1', title: '⏰ Daily report' })
    expect(app.prompt).toHaveBeenCalledTimes(1)
    expect(app.prompt.mock.calls[0]?.[0]).toMatchObject({
      sessionId: 'session-1',
      mode: 'queue',
      content: 'run Daily report: Summarize the working tree.',
    })
  })

  it('settles the run when the session turn completes', async () => {
    const app = harness()
    await app.engine.start(15 * 60_000)
    await app.engine.upsertTask(taskInput())
    await app.fireTimer()
    app.advanceClock(60_000)
    await app.engine.completeTurn('session-1', 'completed')
    const runs = app.engine.listRuns('task-1')
    expect(runs).toHaveLength(1)
    expect(runs[0]).toMatchObject({ status: 'completed', sessionId: 'session-1' })
    expect(app.outcomes).toEqual([{ status: 'completed', taskName: 'Daily report' }])
  })

  it('marks a failed turn and keeps the failure counter', async () => {
    const app = harness()
    await app.engine.start(15 * 60_000)
    await app.engine.upsertTask(taskInput())
    await app.fireTimer()
    await app.engine.completeTurn('session-1', 'failed')
    expect(app.engine.listRuns('task-1')[0]).toMatchObject({ status: 'failed' })
    expect(app.engine.listTasks()[0]).toMatchObject({ consecutiveFailures: 1, enabled: true })
  })

  it('fails the run without a turn when admission breaks', async () => {
    const app = harness({
      gateway: {
        create: vi.fn(async () => {
          throw new Error('controller offline')
        }),
      },
    })
    await app.engine.start(15 * 60_000)
    await app.engine.upsertTask(taskInput())
    await app.fireTimer()
    expect(app.engine.listRuns('task-1')[0]).toMatchObject({
      status: 'failed',
      sessionId: null,
      error: 'controller offline',
    })
    expect(app.engine.activeRun('task-1')).toBeNull()
  })

  it('runs beforePrompt preparation before the task content', async () => {
    const app = harness()
    const order: string[] = []
    app.hooks.beforePrompt = async (task, sessionId) => {
      order.push(`prepare:${sessionId}:${task.permissionPreset ?? 'none'}`)
    }
    const basePrompt = app.prompt
    basePrompt.mockImplementation(async () => {
      order.push('prompt')
      return { accepted: true }
    })
    await app.engine.start(15 * 60_000)
    await app.engine.upsertTask(taskInput({ permissionPreset: 'workspace-write' }))
    await app.fireTimer()
    expect(order).toEqual(['prepare:session-1:workspace-write', 'prompt'])
  })
})

describe('scheduling semantics', () => {
  it('skips a due trigger while the previous run is still active', async () => {
    const app = harness()
    await app.engine.start(15 * 60_000)
    await app.engine.upsertTask(taskInput({ schedule: { kind: 'every', seconds: 60 } }))
    await app.fireTimer()
    app.advanceClock(60_000)
    await app.fireTimer()
    const statuses = app.engine.listRuns('task-1').map(run => run.status)
    expect(statuses).toEqual(['skipped-overlap'])
    expect(app.create).toHaveBeenCalledTimes(1)
  })

  it('queues a second task behind an active run when concurrency is one', async () => {
    const app = harness({ maxConcurrent: 1 })
    await app.engine.start(15 * 60_000)
    await app.engine.upsertTask(taskInput({ id: 'task-1', name: 'First', schedule: { kind: 'every', seconds: 3600 } }))
    await app.engine.upsertTask(taskInput({ id: 'task-2', name: 'Second', schedule: { kind: 'every', seconds: 3600 } }))
    app.advanceClock(3600_000)
    await app.fireTimer()
    expect(app.create).toHaveBeenCalledTimes(1)
    app.advanceClock(60_000)
    await app.engine.completeTurn('session-1', 'completed')
    expect(app.create).toHaveBeenCalledTimes(2)
    app.advanceClock(60_000)
    await app.engine.completeTurn('session-2', 'completed')
    expect(app.create).toHaveBeenCalledTimes(2)
  })

  it('continues one session across runs under the continue policy', async () => {
    const app = harness()
    await app.engine.start(15 * 60_000)
    await app.engine.upsertTask(taskInput({ sessionPolicy: 'continue', schedule: { kind: 'every', seconds: 60 } }))
    await app.fireTimer()
    app.advanceClock(120_000)
    await app.engine.completeTurn('session-1', 'completed')
    app.advanceClock(60_000)
    await app.fireTimer()
    expect(app.create).toHaveBeenCalledTimes(1)
    expect(app.prompt).toHaveBeenCalledTimes(2)
  })

  it('triggers immediately through runNow', async () => {
    const app = harness()
    await app.engine.start(15 * 60_000)
    await app.engine.upsertTask(taskInput())
    expect(app.engine.runNow('task-1')).toBe(true)
    await app.settle()
    expect(app.create).toHaveBeenCalledTimes(1)
    expect(app.engine.runNow('missing')).toBe(false)
  })

  it('never fires disabled tasks from the timer', async () => {
    const app = harness()
    await app.engine.start(15 * 60_000)
    await app.engine.upsertTask(taskInput())
    await app.engine.setTaskEnabled('task-1', false)
    const tasks = app.engine.listTasks()
    expect(tasks[0]?.enabled).toBe(false)
    await app.fireTimer()
    expect(app.create).not.toHaveBeenCalled()
  })
})

describe('misfire recovery', () => {
  it('fires a fresh misfire once inside the recovery window', async () => {
    const due = Date.parse('2026-01-05T01:00:00.000Z')
    const storage: SchedulerTaskFile = {
      version: 1,
      tasks: [{
        ...taskInput(),
        createdAt: '2026-01-04T01:00:00.000Z',
        updatedAt: '2026-01-04T01:00:00.000Z',
        nextRunAt: '2026-01-05T01:00:00.000Z',
        lastRunAt: null,
        consecutiveFailures: 0,
      }],
      runs: {},
    }
    const app = harness({ storage })
    app.nowMs = due + 10 * 60_000
    await app.engine.start(15 * 60_000)
    await app.settle()
    expect(app.create).toHaveBeenCalledTimes(1)
    expect(app.logs.some(message => message.includes('recovery window'))).toBe(true)
  })

  it('expires a stale misfire and advances the schedule', async () => {
    const due = Date.parse('2026-01-05T01:00:00.000Z')
    const storage: SchedulerTaskFile = {
      version: 1,
      tasks: [{
        ...taskInput(),
        createdAt: '2026-01-04T01:00:00.000Z',
        updatedAt: '2026-01-04T01:00:00.000Z',
        nextRunAt: '2026-01-05T01:00:00.000Z',
        lastRunAt: null,
        consecutiveFailures: 0,
      }],
      runs: {},
    }
    const app = harness({ storage })
    app.nowMs = due + 60 * 60_000
    await app.engine.start(15 * 60_000)
    await app.settle()
    expect(app.create).not.toHaveBeenCalled()
    const nextRunAt = Date.parse(app.engine.listTasks()[0]?.nextRunAt ?? '')
    expect(nextRunAt).toBeGreaterThan(app.nowMs)
    expect(app.logs.some(message => message.includes('advances'))).toBe(true)
  })
})

describe('failure pause and teardown', () => {
  async function failing(app: ReturnType<typeof harness>): Promise<void> {
    await app.engine.start(15 * 60_000)
    await app.engine.upsertTask(taskInput({ schedule: { kind: 'every', seconds: 60 } }))
    for (let attempt = 1; attempt <= PAUSE_AFTER_CONSECUTIVE_FAILURES; attempt += 1) {
      await app.fireTimer()
      app.advanceClock(60_000)
      await app.engine.completeTurn(`session-${String(attempt)}`, 'failed')
      if (!app.engine.listTasks()[0]?.enabled) return
    }
  }

  it('pauses a task after the configured consecutive failures', async () => {
    const app = harness()
    await failing(app)
    const task = app.engine.listTasks()[0]
    expect(task?.enabled).toBe(false)
    expect(task?.nextRunAt).toBeNull()
    expect(app.outcomes.filter(outcome => outcome.status === 'failed'))
      .toHaveLength(PAUSE_AFTER_CONSECUTIVE_FAILURES)
  })

  it('resets the failure counter after a completed run', async () => {
    const app = harness()
    await app.engine.start(15 * 60_000)
    await app.engine.upsertTask(taskInput({ schedule: { kind: 'every', seconds: 60 } }))
    await app.fireTimer()
    await app.engine.completeTurn('session-1', 'failed')
    app.advanceClock(60_000)
    await app.fireTimer()
    await app.engine.completeTurn('session-2', 'completed')
    expect(app.engine.listTasks()[0]).toMatchObject({ consecutiveFailures: 0, enabled: true })
  })

  it('cancels outstanding runs and persists on stop', async () => {
    const app = harness()
    await app.engine.start(15 * 60_000)
    await app.engine.upsertTask(taskInput())
    await app.fireTimer()
    await app.engine.stop()
    expect(app.engine.listRuns('task-1')[0]).toMatchObject({ status: 'cancelled' })
    expect(app.timers.every(timer => timer.disposed)).toBe(true)
    expect(app.engine.runNow('task-1')).toBe(false)
  })
})

describe('firing gate', () => {
  it('defers a due trigger while the gate stays closed', async () => {
    const app = harness()
    await app.engine.start(15 * 60_000)
    await app.engine.upsertTask(taskInput())
    app.hooks.mayFireNow = () => false
    await app.fireTimer()
    expect(app.create).not.toHaveBeenCalled()
    const retryTimer = app.timers.at(-1)
    expect(retryTimer?.delayMs).toBe(GATE_RETRY_MS)
    expect(app.engine.activeRun('task-1')).toBeNull()
  })

  it('fires once the gate reopens', async () => {
    const app = harness()
    await app.engine.start(15 * 60_000)
    await app.engine.upsertTask(taskInput())
    let open = false
    app.hooks.mayFireNow = () => open
    await app.fireTimer()
    expect(app.create).not.toHaveBeenCalled()
    open = true
    await app.fireTimer()
    expect(app.create).toHaveBeenCalledTimes(1)
  })
})

describe('turn correlation', () => {
  it('ignores turns of unknown sessions', async () => {
    const app = harness()
    await app.engine.start(15 * 60_000)
    await app.engine.upsertTask(taskInput())
    await app.engine.completeTurn('unknown-session', 'completed')
    expect(app.engine.listRuns('task-1')).toEqual([])
  })

  it('skips a second queued trigger of a continue-policy task while a turn is outstanding', async () => {
    const app = harness()
    await app.engine.start(15 * 60_000)
    await app.engine.upsertTask(taskInput({ sessionPolicy: 'continue', schedule: { kind: 'every', seconds: 60 } }))
    await app.fireTimer()
    app.advanceClock(60_000)
    await app.fireTimer()
    await app.engine.completeTurn('session-1', 'completed')
    const statuses = app.engine.listRuns('task-1').map(run => run.status)
    // History records finalize order: the skip settles first, the earlier
    // trigger's run completes when its turn ends.
    expect(statuses).toEqual(['skipped-overlap', 'completed'])
  })
})

describe('outcome typing', () => {
  it('narrows outcomes to the two terminal turn values', () => {
    const outcomes: TurnOutcome[] = ['completed', 'failed']
    expect(outcomes).toHaveLength(2)
  })
})
