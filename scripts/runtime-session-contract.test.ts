import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it, vi } from 'vitest'
import {
  RuntimeSessionContractError,
  runRuntimeSessionContract,
} from './runtime-session-contract.mjs'
import type { RuntimeSessionContractDriver } from './runtime-session-contract.mjs'
import {
  parseRuntimeSessionContractArgs,
  resolveCandidateRuntimeLayout,
  sanitizeRuntimeSessionContractReport,
} from './run-runtime-session-contract.mjs'

function createRecordingDriver() {
  const calls: string[] = []
  const recordVoid = (name: string) => vi.fn(async (): Promise<void> => {
    calls.push(name)
  })
  const recordValue = <T>(name: string, value: T) => vi.fn(async (): Promise<T> => {
    calls.push(name)
    return value
  })

  const driver: RuntimeSessionContractDriver = {
    start: recordVoid('runtime-start'),
    ready: recordVoid('runtime-ready'),
    createWorkspace: recordValue('workspace-create', 'w-1'),
    createSession: recordValue('session-create', 's-1'),
    requireBinding: recordVoid('session-binding'),
    prompt: recordVoid('session-prompt'),
    open: recordVoid('session-open'),
    waitForEvents: recordVoid('session-event'),
    closeSession: recordVoid('session-close'),
    cleanup: recordVoid('cleanup'),
  }
  return {
    calls,
    driver,
  }
}

describe('runRuntimeSessionContract', () => {
  it('runs the candidate session contract in the required order', async () => {
    const fixture = createRecordingDriver()

    const result = await runRuntimeSessionContract(fixture.driver, { timeoutMs: 100 })

    expect(fixture.calls).toEqual([
      'runtime-start',
      'runtime-ready',
      'workspace-create',
      'session-create',
      'session-binding',
      'session-prompt',
      'session-open',
      'session-event',
      'session-close',
      'cleanup',
    ])
    expect(result).toMatchObject({ ok: true })
    expect(result.stages.map((stage) => stage.stage)).toEqual(fixture.calls)
    expect(result.stages.every((stage) => stage.ok && stage.durationMs >= 0)).toBe(true)
  })

  it.each([
    ['ready', 'runtime-ready', 'timeout'],
    ['requireBinding', 'session-binding', 'binding-missing'],
    ['waitForEvents', 'session-event', 'event-missing'],
  ] as const)('maps %s failure to a stable stage and category', async (method, failedStage, category) => {
    const fixture = createRecordingDriver()
    vi.mocked(fixture.driver[method]).mockRejectedValueOnce(
      new RuntimeSessionContractError(category, `${method} failed`),
    )

    const result = await runRuntimeSessionContract(fixture.driver, { timeoutMs: 100 })

    expect(result).toMatchObject({ ok: false, failedStage, category })
    expect(fixture.driver.cleanup).toHaveBeenCalledTimes(1)
  })

  it('times out one stage and still cleans up exactly once', async () => {
    vi.useFakeTimers()
    try {
      const fixture = createRecordingDriver()
      vi.mocked(fixture.driver.ready).mockImplementationOnce(() => new Promise(() => {}))

      const pending = runRuntimeSessionContract(fixture.driver, { timeoutMs: 20 })
      await vi.advanceTimersByTimeAsync(20)
      const result = await pending

      expect(result).toMatchObject({
        ok: false,
        failedStage: 'runtime-ready',
        category: 'timeout',
      })
      expect(fixture.driver.cleanup).toHaveBeenCalledTimes(1)
    } finally {
      vi.useRealTimers()
    }
  })

  it('preserves the business failure when cleanup also fails', async () => {
    const fixture = createRecordingDriver()
    vi.mocked(fixture.driver.requireBinding).mockRejectedValueOnce(
      new RuntimeSessionContractError('binding-missing', 'binding unavailable'),
    )
    vi.mocked(fixture.driver.cleanup).mockImplementationOnce(async () => {
      fixture.calls.push('cleanup')
      throw new Error('cleanup exploded')
    })

    const result = await runRuntimeSessionContract(fixture.driver, { timeoutMs: 100 })

    expect(result).toMatchObject({
      ok: false,
      failedStage: 'session-binding',
      category: 'binding-missing',
      cleanupFailure: { category: 'cleanup-failed' },
    })
    expect(fixture.driver.cleanup).toHaveBeenCalledTimes(1)
  })

  it('reports cleanup as the primary failure after a successful business flow', async () => {
    const fixture = createRecordingDriver()
    vi.mocked(fixture.driver.cleanup).mockImplementationOnce(async () => {
      fixture.calls.push('cleanup')
      throw new Error('cleanup exploded')
    })

    const result = await runRuntimeSessionContract(fixture.driver, { timeoutMs: 100 })

    expect(result).toMatchObject({
      ok: false,
      failedStage: 'cleanup',
      category: 'cleanup-failed',
    })
  })

  it('does not expose raw error messages in the machine-readable result', async () => {
    const fixture = createRecordingDriver()
    vi.mocked(fixture.driver.prompt).mockRejectedValueOnce(
      new Error('secret prompt and C:\\Users\\private\\project'),
    )

    const result = await runRuntimeSessionContract(fixture.driver, { timeoutMs: 100 })

    expect(result).toMatchObject({
      ok: false,
      failedStage: 'session-prompt',
      category: 'internal',
    })
    expect(JSON.stringify(result)).not.toContain('secret prompt')
    expect(JSON.stringify(result)).not.toContain('C:\\Users\\private')
  })
})

describe('runtime session contract CLI', () => {
  it('requires the candidate root, report path and Runtime version', () => {
    expect(() => parseRuntimeSessionContractArgs([])).toThrow(/--runtime-root/)
    expect(() => parseRuntimeSessionContractArgs(['--runtime-root=C:\\candidate'])).toThrow(/--report/)
    expect(() => parseRuntimeSessionContractArgs([
      '--runtime-root=C:\\candidate',
      '--report=C:\\report.json',
    ])).toThrow(/--runtime-version/)
  })

  it('resolves only the candidate Node and launcher entrypoints', () => {
    const root = mkdtempSync(join(tmpdir(), 'dsh-session-contract-runtime-'))
    try {
      mkdirSync(join(root, 'app'), { recursive: true })
      if (process.platform !== 'win32') mkdirSync(join(root, 'bin'), { recursive: true })
      writeFileSync(join(root, process.platform === 'win32' ? 'node.exe' : 'bin/node'), '', {
        flag: 'w',
      })
      writeFileSync(join(root, 'app', 'launcher.mjs'), '')

      const layout = resolveCandidateRuntimeLayout(root)

      expect(layout.appDirectory).toBe(join(root, 'app'))
      expect(layout.nodeExecutable.startsWith(root)).toBe(true)
      expect(layout.launcher.startsWith(root)).toBe(true)
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  it('removes raw errors, prompts, replies, keys and paths from the report', () => {
    const report = sanitizeRuntimeSessionContractReport({
      ok: false,
      durationMs: 12,
      failedStage: 'session-event',
      category: 'event-missing',
      stages: [{
        stage: 'session-event',
        ok: false,
        durationMs: 10,
        category: 'event-missing',
        message: 'SESSION_CONTRACT_PROMPT SESSION_CONTRACT_PONG sk-secret C:\\Users\\private',
      }],
    }, {
      runtimeVersion: '0.1.10-preview',
      processExitCode: 7,
    })

    expect(report).toEqual({
      schemaVersion: 1,
      runtimeVersion: '0.1.10-preview',
      platform: `${process.platform}-${process.arch}`,
      ok: false,
      durationMs: 12,
      failedStage: 'session-event',
      category: 'event-missing',
      processExitCode: 7,
      stages: [{
        stage: 'session-event',
        ok: false,
        durationMs: 10,
        category: 'event-missing',
      }],
    })
    const serialized = JSON.stringify(report)
    expect(serialized).not.toContain('SESSION_CONTRACT_PROMPT')
    expect(serialized).not.toContain('SESSION_CONTRACT_PONG')
    expect(serialized).not.toContain('sk-secret')
    expect(serialized).not.toContain('C:\\Users\\private')
  })
})
