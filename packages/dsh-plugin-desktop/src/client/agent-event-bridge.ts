import {
  createAgentEventCheckpoint,
  isAgentEventEnvelope,
  type AgentEventEnvelope,
} from './agent-events'

export interface AgentEventBridgeOptions {
  parent: () => Window
  targetOrigin: string
  onEvent(event: AgentEventEnvelope): void
  onReplayRequest(request: {
    taskId: string
    sessionId: string
    expectedSequence: number
  }): void
}

export function createAgentEventBridge(options: AgentEventBridgeOptions) {
  const checkpoint = createAgentEventCheckpoint()
  return {
    onMessage(event: MessageEvent): void {
      if (event.source !== options.parent() || event.origin !== options.targetOrigin) return
      if (!isAgentEventEnvelope(event.data)) return
      const result = checkpoint.accept(event.data)
      if (result.status === 'accepted') {
        options.onEvent(result.event)
      } else if (result.status === 'gap') {
        options.onReplayRequest({
          taskId: event.data.taskId,
          sessionId: event.data.sessionId,
          expectedSequence: result.expectedSequence,
        })
      }
    },
  }
}
