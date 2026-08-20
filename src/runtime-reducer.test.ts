import { describe, expect, it } from 'vitest'
import { initialRuntimeState, runtimeReducer, type RuntimeViewState } from './runtime-reducer'

describe('runtimeReducer', () => {
  it('ignores an event from a superseded generation', () => {
    const active = { ...initialRuntimeState, generationId: 'g-2', phase: 'active' as const }
    const next = runtimeReducer(active, {
      type: 'desktop-event',
      event: {
        kind: 'generation-failed',
        generationId: 'g-1',
        failure: { code: 'process', message: 'late', recoverable: true },
      },
    })
    expect(next).toBe(active)
  })

  it('restores a ready renderer from a repeated bootstrap reply', () => {
    const next = runtimeReducer(initialRuntimeState, {
      type: 'bootstrap-started',
      reply: {
        operationId: 'op-ready',
        phase: 'ready',
        rendererUrl: 'http://127.0.0.1:39000/?dsh-desktop-mode=advanced',
      },
    })

    expect(next).toMatchObject({
      operationId: 'op-ready',
      phase: 'ready',
      rendererUrl: expect.stringContaining('127.0.0.1:39000'),
    })
  })

  it('accepts progress from the active operation', () => {
    const state: RuntimeViewState = { ...initialRuntimeState, operationId: 'op-1' }
    const next = runtimeReducer(state, {
      type: 'runtime-event',
      event: {
        kind: 'progress',
        payload: {
          operationId: 'op-1', phase: 'downloading', completed: 20, total: 100, message: '下载中',
        },
      },
    })
    expect(next).toMatchObject({ phase: 'downloading', progress: { completed: 20, total: 100 }, message: '下载中' })
  })

  it('ignores stale operation events', () => {
    const state: RuntimeViewState = { ...initialRuntimeState, operationId: 'op-new' }
    const next = runtimeReducer(state, {
      type: 'runtime-event',
      event: {
        kind: 'progress',
        payload: { operationId: 'op-old', phase: 'ready', completed: 1, total: 1, message: '旧事件' },
      },
    })
    expect(next).toBe(state)
  })

  it('moves cancellation failures to recovery', () => {
    const state: RuntimeViewState = { ...initialRuntimeState, operationId: 'op-1' }
    const next = runtimeReducer(state, {
      type: 'runtime-event',
      event: {
        kind: 'failure', operationId: 'op-1',
        payload: { code: 'cancelled', message: '已取消', recoverable: true },
      },
    })
    expect(next.phase).toBe('failed')
    expect(next.error?.code).toBe('cancelled')
  })

  it('accepts renderer URL only from the active ready operation', () => {
    const state: RuntimeViewState = { ...initialRuntimeState, operationId: 'op-1' }
    const next = runtimeReducer(state, {
      type: 'runtime-event',
      event: {
        kind: 'ready', operationId: 'op-1',
        rendererUrl: 'http://127.0.0.1:39000/?dsh-desktop-mode=advanced',
      },
    })

    expect(next).toMatchObject({
      phase: 'ready',
      rendererUrl: expect.stringContaining('127.0.0.1:39000'),
    })
  })

  it('clears a ready renderer when the active process fails', () => {
    const state = {
      ...initialRuntimeState,
      phase: 'ready' as const,
      operationId: 'op-1',
      rendererUrl: 'http://127.0.0.1:39000/',
    }
    const next = runtimeReducer(state, {
      type: 'runtime-event',
      event: {
        kind: 'failure', operationId: 'op-1',
        payload: { code: 'process', message: '进程已退出', recoverable: true },
      },
    })

    expect(next.rendererUrl).toBeNull()
    expect(next.phase).toBe('failed')
  })

  it('clears a ready renderer when a local request fails', () => {
    const state = {
      ...initialRuntimeState,
      phase: 'ready' as const,
      rendererUrl: 'http://127.0.0.1:39000/',
    }
    const next = runtimeReducer(state, {
      type: 'request-failed',
      error: { code: 'internal', message: '本地请求失败', recoverable: true },
    })

    expect(next.rendererUrl).toBeNull()
    expect(next.phase).toBe('failed')
  })

  it('resets the stale failure message when a new bootstrap starts', () => {
    let state = runtimeReducer(initialRuntimeState, {
      type: 'request-failed',
      error: { code: 'network', message: 'raw boom', recoverable: true },
    })
    state = runtimeReducer(state, {
      type: 'bootstrap-started',
      reply: { operationId: 'op-2', phase: 'checking', rendererUrl: null },
    })

    expect(state.error).toBeNull()
    expect(state.message).toBe('正在检查 DeepSeek Harness…')
  })
})
