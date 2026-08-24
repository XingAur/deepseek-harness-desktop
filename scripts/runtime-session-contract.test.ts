import { describe, expect, it, vi } from 'vitest'
import {
  RuntimeSessionContractError,
  runRuntimeSessionContract,
} from './runtime-session-contract.mjs'

function createRecordingDriver() {
  const calls: string[] = []
  const record = <T>(name: string, value?: T) => vi.fn(async () => {
    calls.push(name)
    return value as T
  })

  return {
    calls,
    driver: {
      start: record('runtime-start'),
      ready: record('runtime-ready'),
      createWorkspace: record('workspace-create', 'w-1'),
      createSession: record('session-create', 's-1'),
      requireBinding: record('session-binding'),
      prompt: record('session-prompt'),
      open: record('session-open'),
      waitForEvents: record('session-event'),
      closeSession: record('session-close'),
      cleanup: record('cleanup'),
    },
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
