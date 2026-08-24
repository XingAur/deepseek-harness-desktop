import { describe, expect, it, vi } from 'vitest'
import { AGENT_EVENT_CHANNEL } from '../src/client/agent-events'
import { createAgentEventBridge } from '../src/client/agent-event-bridge'

const event = (sequence: number) => ({
  channel: AGENT_EVENT_CHANNEL,
  generationId: 'generation-1',
  taskId: 'task-1',
  sessionId: 'session-1',
  sequence,
  type: 'task.progress',
  payload: { percent: sequence },
})

describe('plugin agent event bridge', () => {
  it('requires the parent source and exact origin, suppresses duplicates, and requests replay on gaps', () => {
    const parent = {} as Window
    const onEvent = vi.fn()
    const onReplayRequest = vi.fn()
    const bridge = createAgentEventBridge({
      parent: () => parent,
      targetOrigin: 'http://127.0.0.1:39000',
      onEvent,
      onReplayRequest,
    })

    bridge.onMessage({ source: {}, origin: 'http://127.0.0.1:39000', data: event(1) } as MessageEvent)
    bridge.onMessage({ source: parent, origin: 'http://127.0.0.1:39001', data: event(1) } as MessageEvent)
    bridge.onMessage({ source: parent, origin: 'http://127.0.0.1:39000', data: event(1) } as MessageEvent)
    bridge.onMessage({ source: parent, origin: 'http://127.0.0.1:39000', data: event(1) } as MessageEvent)
    bridge.onMessage({ source: parent, origin: 'http://127.0.0.1:39000', data: event(3) } as MessageEvent)

    expect(onEvent).toHaveBeenCalledTimes(1)
    expect(onReplayRequest).toHaveBeenCalledWith({
      taskId: 'task-1',
      sessionId: 'session-1',
      expectedSequence: 2,
    })
  })
})
