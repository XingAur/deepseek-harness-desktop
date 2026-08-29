import { describe, expect, it } from 'vitest'
import { chooseExecutorId, selectHarnessExecutor } from '../src/server/harness-executor-selection'
import { configuredDeepSeekReviewerExecutor, configuredOpenAICompatibleExecutor, executorHintForModel } from '../src/server/harness-host'

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

  it('registers DeepSeek only when the host has a key', () => {
    expect(Object.keys(configuredDeepSeekReviewerExecutor({}))).toEqual([])
    expect(Object.keys(configuredDeepSeekReviewerExecutor({ DSH_DEEPSEEK_API_KEY: 'test-key' }))).toEqual(['deepseek'])
  })

  it('routes a provider-namespaced model to its executor without restricting other models', () => {
    expect(executorHintForModel('deepseek-reasoner')).toBe('deepseek')
    expect(executorHintForModel('  DeepSeek-Chat  ')).toBe('deepseek')
    expect(executorHintForModel('gpt-5.6-sol')).toBeUndefined()
    expect(executorHintForModel('')).toBeUndefined()
    expect(executorHintForModel(undefined)).toBeUndefined()
    // 模型提示只在任务未显式选择后端时生效，显式选择仍然优先。
    expect(chooseExecutorId({ requestedExecutor: executorHintForModel('deepseek-reasoner') ?? undefined })).toBe('deepseek')
    expect(chooseExecutorId({ requestedExecutor: 'codex', configuredExecutor: undefined })).toBe('codex')
  })

  it('registers the generic openai-compatible executor for any endpoint and model', () => {
    expect(Object.keys(configuredOpenAICompatibleExecutor({}, 'qwen-max'))).toEqual([])
    expect(
      Object.keys(configuredOpenAICompatibleExecutor({ DSH_OPENAI_API_KEY: 'key' }, undefined)),
    ).toEqual([])
    const registered = configuredOpenAICompatibleExecutor(
      { DSH_OPENAI_API_KEY: 'key', DSH_OPENAI_BASE_URL: 'https://llm.internal/v1' },
      'qwen-max',
    )
    expect(Object.keys(registered)).toEqual(['openai-compatible'])
  })
})
