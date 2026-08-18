import { describe, expect, it } from 'vitest'
import { initialRuntimeState, runtimeReducer, type RuntimeViewState } from './runtime-reducer'

describe('runtimeReducer', () => {
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
})
