import { describe, expect, it } from 'vitest'
import {
  DESKTOP_BRIDGE_V2_CHANNEL,
  bridgeCommandByActionV2,
  isVersionedBridgePayload,
  isVersionedBridgeRequest,
  isVersionedBridgeResponse,
} from './bridge-contract'

const request = {
  channel: DESKTOP_BRIDGE_V2_CHANNEL,
  requestId: 'request-1',
  generationId: 'generation-1',
  sessionId: 'session-1',
  action: 'task.create',
  payload: { workspaceId: 'workspace-1', prompt: '检查项目', permission: 'request-approval' },
}

describe('dsh-desktop/v2 bridge contract', () => {
  it('accepts a strict versioned task request and maps it to a named command', () => {
    expect(isVersionedBridgeRequest(request)).toBe(true)
    expect(bridgeCommandByActionV2['task.create']).toBe('agent_task_create')
  })

  it('accepts only paired, identifier-safe provider and agent selections', () => {
    expect(isVersionedBridgeRequest({
      ...request,
      payload: {
        ...request.payload,
        providerId: 'claude',
        agentId: 'claude:default',
      },
    })).toBe(true)
    expect(isVersionedBridgeRequest({
      ...request,
      payload: { ...request.payload, providerId: 'claude' },
    })).toBe(false)
    expect(isVersionedBridgeRequest({
      ...request,
      payload: { ...request.payload, agentId: 'claude:../escape', providerId: 'claude' },
    })).toBe(false)
  })

  it('accepts an empty task-list query without widening task mutation payloads', () => {
    expect(isVersionedBridgePayload('task.list', {})).toBe(true)
    expect(isVersionedBridgeRequest({ ...request, action: 'task.list', payload: {} })).toBe(true)
    expect(isVersionedBridgeRequest({ ...request, action: 'task.list', payload: { taskId: 'task-1' } })).toBe(false)
    expect(bridgeCommandByActionV2['task.list']).toBe('agent_task_list')
  })

  it('requires a bounded non-empty task prompt', () => {
    expect(isVersionedBridgePayload('task.create', {
      workspaceId: 'workspace-1',
      prompt: '检查项目',
      permission: 'request-approval',
    })).toBe(true)
    expect(isVersionedBridgePayload('task.create', {
      workspaceId: 'workspace-1',
      prompt: '   ',
      permission: 'request-approval',
    })).toBe(false)
    expect(isVersionedBridgePayload('task.create', {
      workspaceId: 'workspace-1',
      prompt: 'x'.repeat(16 * 1024 + 1),
      permission: 'request-approval',
    })).toBe(false)
  })

  it('rejects unknown actions, fields, malformed ids, and oversized payloads', () => {
    expect(isVersionedBridgeRequest({ ...request, action: 'shell.execute' })).toBe(false)
    expect(isVersionedBridgeRequest({ ...request, unexpected: true })).toBe(false)
    expect(isVersionedBridgeRequest({ ...request, payload: { workspaceId: 'workspace-1', prompt: '检查项目', permission: 'request-approval', shell: 'rm -rf' } })).toBe(false)
    expect(isVersionedBridgeRequest({ ...request, generationId: '../escape' })).toBe(false)
    expect(isVersionedBridgeRequest({
      ...request,
      payload: { output: 'x'.repeat(33 * 1024) },
    })).toBe(false)
  })

  it('keeps content-reference reads within the host-side 16 KiB bound', () => {
    const contentRequest = {
      ...request,
      action: 'content-reference.read',
      payload: { contentRefId: 'ref-1', taskId: 'task-1', offset: 0, length: 16 * 1024 },
    }
    expect(isVersionedBridgeRequest(contentRequest)).toBe(true)
    expect(isVersionedBridgeRequest({
      ...contentRequest,
      payload: { ...contentRequest.payload, length: 16 * 1024 + 1 },
    })).toBe(false)
  })

  it('rejects secret-shaped results before they reach the iframe', () => {
    expect(isVersionedBridgeResponse({
      channel: DESKTOP_BRIDGE_V2_CHANNEL,
      requestId: request.requestId,
      generationId: request.generationId,
      sessionId: request.sessionId,
      ok: true,
      result: { secret: 'must-not-cross' },
    })).toBe(false)
  })

  it('allows a credential to be explicitly bound to a provider without accepting unsafe ids', () => {
    expect(isVersionedBridgePayload('credential.put', { providerId: 'codex', secret: 'private-value' })).toBe(true)
    expect(isVersionedBridgePayload('credential.put', { providerId: '../escape', secret: 'private-value' })).toBe(false)
  })
})
