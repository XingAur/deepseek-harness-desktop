import { describe, expect, it } from 'vitest'
import {
  AGENT_EVENT_CHANNEL,
  createAgentEventCheckpoint,
  isAgentEventEnvelope,
} from './agent-events'

const event = (sequence: number) => ({
  channel: AGENT_EVENT_CHANNEL,
  generationId: 'generation-1',
  taskId: 'task-1',
  sessionId: 'session-1',
  sequence,
  type: 'task.progress',
  payload: { percent: sequence },
})

describe('agent event envelopes', () => {
  it('validates a bounded event envelope without allowing secret fields', () => {
    expect(isAgentEventEnvelope(event(1))).toBe(true)
    expect(isAgentEventEnvelope({ ...event(2), payload: { token: 'hidden' } })).toBe(false)
    expect(isAgentEventEnvelope({ ...event(2), payload: { output: 'x'.repeat(33 * 1024) } })).toBe(false)
  })

  it('suppresses duplicates and requests replay after a sequence gap', () => {
    const checkpoint = createAgentEventCheckpoint()
    expect(checkpoint.accept(event(1))).toEqual({ status: 'accepted', event: event(1) })
    expect(checkpoint.accept(event(1))).toEqual({ status: 'duplicate' })
    expect(checkpoint.accept(event(3))).toEqual({ status: 'gap', expectedSequence: 2 })
    expect(checkpoint.accept(event(2))).toEqual({ status: 'accepted', event: event(2) })
  })
})
