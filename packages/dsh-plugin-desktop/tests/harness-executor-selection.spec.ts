import { describe, expect, it } from 'vitest'
import { chooseExecutorId, selectHarnessExecutor } from '../src/server/harness-executor-selection'

describe('Harness executor selection', () => {
  it('prefers an explicit task backend over the environment', () => {
    expect(chooseExecutorId({ requestedExecutor: 'deepseek', configuredExecutor: 'codex' })).toBe('deepseek')
  })

  it('uses the environment when the task keeps the host bridge default', () => {
    expect(chooseExecutorId({ requestedExecutor: 'host-bridge', configuredExecutor: 'deepseek' })).toBe('deepseek')
  })

  it('keeps the legacy Codex default only when nothing selects another executor', () => {
    expect(chooseExecutorId({ requestedExecutor: 'host-bridge' })).toBe('codex')
  })

  it('does not silently fall back when the selected executor is unavailable', async () => {
    const selection = selectHarnessExecutor({
      requestedExecutor: 'deepseek',
      configuredExecutor: 'codex',
      executors: {},
    })
    expect(selection.id).toBe('deepseek')
    await expect(selection.execute({} as never, {} as never)).resolves.toMatchObject({ errorCode: 'worker_backend_unavailable' })
  })

  it('binds an injected executor for a provider-neutral host', async () => {
    const execute = async () => ({ finalResponse: { schema_version: 'test.v1' } })
    const selection = selectHarnessExecutor({ requestedExecutor: 'deepseek', executors: { deepseek: execute } })
    expect(selection.execute).toBe(execute)
    await expect(selection.execute({} as never, {} as never)).resolves.toEqual({ finalResponse: { schema_version: 'test.v1' } })
  })
})
