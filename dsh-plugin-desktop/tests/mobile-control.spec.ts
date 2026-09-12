import { describe, expect, it } from 'vitest'
import { MobileControlCache, type MobileControlControllerFace } from '../src/mobile-control.ts'

function fakeController(frames: readonly unknown[]): MobileControlControllerFace & { controller: AbortController } {
  let controller = new AbortController()
  const face: MobileControlControllerFace & { controller: AbortController } = {
    get controller() { return controller },
    control(signal) {
      controller = new AbortController()
      void signal
      return (async function* yieldFrames() {
        for (const frame of frames) yield frame
      })()
    },
  }
  return face
}

const baselineFrame = {
  type: 'baseline',
  value: {
    queues: {
      s1: [{ id: 'q1', placement: 'queued', message: { id: 'q1', content: [{ type: 'text', text: 'do the next thing' }] } }],
    },
    projections: {
      s1: { values: { permissions: { currentValue: 'read-only', options: [{ value: 'read-only', name: 'Read only' }] } } },
      s2: { values: { modelSelection: { next: { provider: 'p', model: 'm' } } } },
    },
  },
}

describe('MobileControlCache', () => {
  it('serves the baseline snapshot per session', async () => {
    const cache = new MobileControlCache(() => undefined)
    const face = fakeController([baselineFrame])
    cache.ensure(face)
    // The async generator yields on the first microtask drain.
    await new Promise(resolve => { setTimeout(resolve, 0) })
    const info = cache.sessionInfo('s1')
    expect(info).not.toBeNull()
    expect(info?.queue).toEqual([{ id: 'q1', placement: 'queued', preview: 'do the next thing' }])
    expect(info?.permissions).toEqual({ currentValue: 'read-only', options: [{ value: 'read-only', name: 'Read only' }] })
    expect(info?.modelSelection).toBeNull()
    expect(cache.sessionInfo('s2')?.modelSelection).toEqual({ next: { provider: 'p', model: 'm' } })
    expect(cache.sessionInfo('missing')).toBeNull()
    cache.dispose()
  })

  it('applies incremental queue and projection frames', async () => {
    const cache = new MobileControlCache(() => undefined)
    const face = fakeController([
      baselineFrame,
      { type: 'queue', sessionId: 's1', items: [] },
      { type: 'projection', sessionId: 's1', key: 'contextPressure', value: { projectedTokens: 120, contextWindow: 200 } },
    ])
    cache.ensure(face)
    await new Promise(resolve => { setTimeout(resolve, 0) })
    const info = cache.sessionInfo('s1')
    expect(info?.queue).toEqual([])
    expect(info?.contextPressure).toEqual({ projectedTokens: 120, contextWindow: 200 })
    expect(info?.permissions).toEqual({ currentValue: 'read-only', options: [{ value: 'read-only', name: 'Read only' }] })
    cache.dispose()
  })

  it('ignores ensure without a controller and survives dispose', () => {
    const cache = new MobileControlCache(() => undefined)
    cache.ensure(undefined)
    expect(cache.sessionInfo('s1')).toBeNull()
    cache.dispose()
  })
})
