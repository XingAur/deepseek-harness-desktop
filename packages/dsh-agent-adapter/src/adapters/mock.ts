import type { AgentEvent, AdapterRequest } from '../protocol.js'

export function mockSessionEvents(request: AdapterRequest): AgentEvent[] {
  return [
    frame(request, 'session.started'),
    frame(request, 'session.completed'),
  ]
}

function frame(request: AdapterRequest, type: 'session.started' | 'session.completed'): AgentEvent {
  return {
    protocolVersion: request.protocolVersion,
    requestId: request.requestId,
    sessionId: request.sessionId,
    sequence: 0,
    type,
    payload: {},
  }
}
