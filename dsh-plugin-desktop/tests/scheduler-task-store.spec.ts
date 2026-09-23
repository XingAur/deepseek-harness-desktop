import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import {
  MAX_TASK_FILE_BYTES,
  readSchedulerTaskFile,
  schedulerTasksPath,
  writeSchedulerTaskFile,
} from '../src/scheduler/task-store.ts'
import {
  emptySchedulerTaskFile,
  normalizeScheduledTask,
  normalizeSchedulerTaskFile,
  normalizeScheduleRule,
  truncateTaskRuns,
  type ScheduledTask,
  type TaskRun,
} from '../src/scheduler/types.ts'

function validTask(overrides: Partial<ScheduledTask> = {}): ScheduledTask {
  return {
    id: 'task-1',
    name: 'Daily report',
    prompt: 'Summarize the working tree.',
    cwd: null,
    schedule: { kind: 'cron', expression: '0 9 * * 1-5' },
    enabled: true,
    sessionPolicy: 'fresh',
    permissionPreset: null,
    overlap: 'skip',
    notify: true,
    createdAt: '2026-01-05T01:00:00.000Z',
    updatedAt: '2026-01-05T01:00:00.000Z',
    nextRunAt: '2026-01-06T01:00:00.000Z',
    lastRunAt: null,
    consecutiveFailures: 0,
    ...overrides,
  }
}

function run(status: TaskRun['status'], startedAt: string, overrides: Partial<TaskRun> = {}): TaskRun {
  return {
    taskId: 'task-1',
    sessionId: 'session-1',
    requestId: 'request-1',
    startedAt,
    finishedAt: null,
    status,
    error: null,
    ...overrides,
  }
}

describe('schedule rule normalization', () => {
  it('keeps valid fixed intervals at or above the minimum', () => {
    expect(normalizeScheduleRule({ kind: 'every', seconds: 60 })).toEqual({ kind: 'every', seconds: 60 })
    expect(normalizeScheduleRule({ kind: 'every', seconds: 59 })).toBeNull()
    expect(normalizeScheduleRule({ kind: 'every', seconds: 90.5 })).toBeNull()
  })

  it('trims and bounds cron expressions', () => {
    expect(normalizeScheduleRule({ kind: 'cron', expression: '  0 9 * * *  ' }))
      .toEqual({ kind: 'cron', expression: '0 9 * * *' })
    expect(normalizeScheduleRule({ kind: 'cron', expression: '   ' })).toBeNull()
    expect(normalizeScheduleRule({ kind: 'cron', expression: '0'.repeat(257) })).toBeNull()
    expect(normalizeScheduleRule({ kind: 'other' })).toBeNull()
    expect(normalizeScheduleRule('0 9 * * *')).toBeNull()
  })
})

describe('task normalization', () => {
  it('accepts a complete stored task unchanged', () => {
    expect(normalizeScheduledTask(validTask())).toEqual(validTask())
  })

  it('applies defaults and drops invalid tasks', () => {
    expect(normalizeScheduledTask({ ...validTask(), enabled: false })?.enabled).toBe(false)
    expect(normalizeScheduledTask({ ...validTask(), enabled: 'no' })?.enabled).toBe(true)
    expect(normalizeScheduledTask({ ...validTask(), sessionPolicy: 'continue' })?.sessionPolicy).toBe('continue')
    expect(normalizeScheduledTask({ ...validTask(), sessionPolicy: 'weird' })?.sessionPolicy).toBe('fresh')
    expect(normalizeScheduledTask({ ...validTask(), prompt: '   ' })).toBeNull()
    expect(normalizeScheduledTask({ ...validTask(), id: '' })).toBeNull()
    expect(normalizeScheduledTask({ ...validTask(), createdAt: 'yesterday' })).toBeNull()
    expect(normalizeScheduledTask({ ...validTask(), schedule: null })).toBeNull()
    expect(normalizeScheduledTask([])).toBeNull()
    expect(normalizeScheduledTask(null)).toBeNull()
  })

  it('normalizes optional instants and bounds counters', () => {
    const task = normalizeScheduledTask({
      ...validTask(),
      updatedAt: 'not-a-date',
      nextRunAt: undefined,
      lastRunAt: 'garbage',
      consecutiveFailures: -3,
      cwd: 42,
      permissionPreset: 'safe',
    })
    expect(task).toMatchObject({
      updatedAt: '2026-01-05T01:00:00.000Z',
      nextRunAt: null,
      lastRunAt: null,
      consecutiveFailures: 0,
      cwd: null,
      permissionPreset: 'safe',
    })
  })
})

describe('document normalization', () => {
  it('recovers the empty document from unreadable roots', () => {
    expect(normalizeSchedulerTaskFile(null, 50)).toEqual(emptySchedulerTaskFile())
    expect(normalizeSchedulerTaskFile('text', 50)).toEqual(emptySchedulerTaskFile())
    expect(normalizeSchedulerTaskFile({ tasks: 'nope', runs: 'nope' }, 50)).toEqual(emptySchedulerTaskFile())
  })

  it('drops only the invalid entries and orphans', () => {
    const good = validTask()
    const document = normalizeSchedulerTaskFile({
      version: 99,
      tasks: [good, { id: 'bad' }, null],
      runs: {
        'task-1': [run('completed', '2026-01-06T01:10:00.000Z'), { broken: true }],
        'missing-task': [run('failed', '2026-01-06T02:00:00.000Z')],
      },
    }, 50)
    expect(document.version).toBe(1)
    expect(document.tasks).toEqual([good])
    expect(Object.keys(document.runs)).toEqual(['task-1'])
    expect(document.runs['task-1']).toHaveLength(1)
  })

  it('keeps only the newest runs within the per-task bound', () => {
    const entries = Array.from({ length: 7 }, (_, index) => run(
      'completed',
      `2026-01-0${index + 1}T00:00:00.000Z`,
      { requestId: `request-${String(index)}` },
    ))
    expect(truncateTaskRuns(entries, 5)).toHaveLength(5)
    expect(truncateTaskRuns(entries, 5)[0]?.startedAt).toBe('2026-01-03T00:00:00.000Z')
    expect(truncateTaskRuns(entries, 0)).toEqual([])
  })
})

describe('task file storage', () => {
  let directory = ''

  beforeEach(() => {
    directory = mkdtempSync(join(tmpdir(), 'dsh-scheduler-'))
  })

  afterEach(() => {
    rmSync(directory, { recursive: true, force: true })
  })

  it('reports missing storage as a fresh document', async () => {
    const path = schedulerTasksPath(directory)
    const read = await readSchedulerTaskFile(path)
    expect(read.recovered).toBe('missing')
    expect(read.file).toEqual(emptySchedulerTaskFile())
  })

  it('round-trips a document through the atomic writer', async () => {
    const path = schedulerTasksPath(directory)
    const task = validTask({ id: 'stored' })
    await writeSchedulerTaskFile(path, { version: 1, tasks: [task], runs: {} })
    const read = await readSchedulerTaskFile(path)
    expect(read.recovered).toBe(false)
    expect(read.file.tasks).toEqual([task])
    // The document holds user-authored prompts, so the file stays private.
    const mode = (await import('node:fs/promises')).stat
    expect((await mode(path)).mode & 0o777).toBe(0o600)
  })

  it('recovers corrupt and oversized documents to empty', async () => {
    const corruptPath = join(directory, 'corrupt-tasks.json')
    writeFileSync(corruptPath, '{"version":1,"tasks":[{"id":"partial"}')
    const corrupt = await readSchedulerTaskFile(corruptPath)
    expect(corrupt.recovered).toBe('corrupt')
    expect(corrupt.file.tasks).toEqual([])

    const oversizedPath = join(directory, 'oversized-tasks.json')
    writeFileSync(oversizedPath, 'x'.repeat(MAX_TASK_FILE_BYTES + 1))
    const oversized = await readSchedulerTaskFile(oversizedPath)
    expect(oversized.recovered).toBe('oversized')
    expect(oversized.file).toEqual(emptySchedulerTaskFile())
  })

  it('normalizes each stored entry on read', async () => {
    const path = join(directory, 'normalize-tasks.json')
    const good = validTask({ id: 'good' })
    writeFileSync(path, JSON.stringify({
      version: 1,
      tasks: [good, { id: 'broken', name: 'Broken' }],
      runs: { good: [{ taskId: 'good', requestId: 'r', startedAt: 'nope', status: 'nope' }] },
    }))
    const read = await readSchedulerTaskFile(path)
    expect(read.recovered).toBe(false)
    expect(read.file.tasks).toEqual([good])
    expect(read.file.runs).toEqual({})
  })

  it('writes pretty-printed JSON with a trailing newline', async () => {
    const path = join(directory, 'format', 'tasks.json')
    await writeSchedulerTaskFile(path, emptySchedulerTaskFile())
    const text = readFileSync(path, 'utf8')
    expect(text.startsWith('{\n  "version": 1,')).toBe(true)
    expect(text.endsWith('\n')).toBe(true)
  })
})
