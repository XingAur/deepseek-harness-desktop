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

it('maps the codex cli lifecycle actions to dedicated commands with provider payloads', () => {
    expect(bridgeCommandByActionV2['cli.install.status']).toBe('agent_cli_install_status')
    expect(bridgeCommandByActionV2['cli.install.start']).toBe('agent_cli_install_start')
    expect(bridgeCommandByActionV2['cli.login.status']).toBe('agent_cli_login_status')
    expect(bridgeCommandByActionV2['cli.login.start']).toBe('agent_cli_login_start')
    for (const action of ['cli.install.status', 'cli.install.start', 'cli.login.status', 'cli.login.start'] as const) {
      expect(isVersionedBridgePayload(action, { providerId: 'codex' })).toBe(true)
      expect(isVersionedBridgePayload(action, { providerId: '../escape' })).toBe(false)
      expect(isVersionedBridgePayload(action, { providerId: 'codex', command: ['npm'] })).toBe(false)
      expect(isVersionedBridgeRequest({ ...request, action, payload: { providerId: 'codex' } })).toBe(true)
    }
  })

it('maps plugin market actions to dedicated commands with bounded payloads', () => {
    expect(bridgeCommandByActionV2['plugin.catalog.list']).toBe('agent_plugin_catalog')
    expect(bridgeCommandByActionV2['plugin.install.start']).toBe('agent_plugin_install_start')
    expect(bridgeCommandByActionV2['plugin.install.status']).toBe('agent_plugin_install_status')
    expect(isVersionedBridgePayload('plugin.catalog.list', {})).toBe(true)
    expect(isVersionedBridgePayload('plugin.catalog.list', { query: '余额', category: 'tools', offset: 0, limit: 30 })).toBe(true)
    expect(isVersionedBridgePayload('plugin.catalog.list', { query: 'x'.repeat(121) })).toBe(false)
    expect(isVersionedBridgePayload('plugin.catalog.list', { limit: 51 })).toBe(false)
    expect(isVersionedBridgePayload('plugin.catalog.list', { pluginId: 'a/b' })).toBe(false)
    expect(isVersionedBridgePayload('plugin.install.start', { pluginId: 'owner/repo' })).toBe(true)
    expect(isVersionedBridgePayload('plugin.install.start', { pluginId: '../escape' })).toBe(false)
    expect(isVersionedBridgePayload('plugin.install.status', { pluginId: 'a/b' })).toBe(true)
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

describe('prompts v2 actions', () => {
  it('accepts prompts.save with bounded title and content', () => {
    expect(isVersionedBridgePayload('prompts.save', { presetId: undefined, title: '标题', content: '正文' })).toBe(true)
    expect(isVersionedBridgePayload('prompts.save', { title: '标题', content: '正文' })).toBe(true)
    expect(isVersionedBridgePayload('prompts.save', { presetId: 'p1', title: '标题', content: 'x'.repeat(24 * 1024) })).toBe(true)
    expect(isVersionedBridgePayload('prompts.save', { title: '标题', content: 'x'.repeat(24 * 1024 + 1) })).toBe(false)
    expect(isVersionedBridgePayload('prompts.save', { title: '', content: '正文' })).toBe(false)
    expect(isVersionedBridgePayload('prompts.save', { title: 'x'.repeat(201), content: '正文' })).toBe(false)
    expect(isVersionedBridgePayload('prompts.save', { title: '标题', content: '正文', extra: 1 })).toBe(false)
  })

  it('accepts prompts.resolve-conflict with the same bounds as save', () => {
    expect(isVersionedBridgePayload('prompts.resolve-conflict', { presetId: 'p1', title: '标题', content: '正文' })).toBe(true)
    expect(isVersionedBridgePayload('prompts.resolve-conflict', { presetId: 'p1', title: '标题', content: 'x'.repeat(24 * 1024 + 1) })).toBe(false)
    expect(isVersionedBridgePayload('prompts.resolve-conflict', { title: '标题', content: '正文' })).toBe(false)
  })

  it('accepts prompts.activate/deactivate with known targets only', () => {
    expect(isVersionedBridgePayload('prompts.activate', { presetId: 'p1', target: 'claude' })).toBe(true)
    expect(isVersionedBridgePayload('prompts.activate', { presetId: 'p1', target: 'gemini' })).toBe(false)
    expect(isVersionedBridgePayload('prompts.deactivate', { target: 'dsh' })).toBe(true)
    expect(isVersionedBridgePayload('prompts.deactivate', {})).toBe(false)
  })

  it('accepts prompts.import with a deduplicated target list', () => {
    expect(isVersionedBridgePayload('prompts.import', { targets: ['claude', 'codex'] })).toBe(true)
    expect(isVersionedBridgePayload('prompts.import', { targets: ['claude', 'claude'] })).toBe(false)
    expect(isVersionedBridgePayload('prompts.import', { targets: [] })).toBe(false)
    expect(isVersionedBridgePayload('prompts.import', { targets: ['claude', 'codex'], extra: 1 })).toBe(false)
  })

  it('accepts prompts.get/delete/status/list payloads', () => {
    expect(isVersionedBridgePayload('prompts.get', { presetId: 'p1' })).toBe(true)
    expect(isVersionedBridgePayload('prompts.delete', { presetId: 'p1' })).toBe(true)
    expect(isVersionedBridgePayload('prompts.status', {})).toBe(true)
    expect(isVersionedBridgePayload('prompts.list', {})).toBe(true)
    expect(isVersionedBridgePayload('prompts.get', {})).toBe(false)
  })

  it('maps prompts actions to tauri commands', () => {
    expect(bridgeCommandByActionV2['prompts.list']).toBe('prompts_list')
    expect(bridgeCommandByActionV2['prompts.resolve-conflict']).toBe('prompts_resolve_conflict')
    expect(bridgeCommandByActionV2['prompts.import']).toBe('prompts_import')
  })
})
