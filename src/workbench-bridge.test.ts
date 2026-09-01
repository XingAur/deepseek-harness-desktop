import { describe, expect, it, vi } from 'vitest'
import { DESKTOP_BRIDGE_CHANNEL, DESKTOP_BRIDGE_V2_CHANNEL } from './bridge-contract'
import { AGENT_EVENT_CHANNEL } from './agent-events'
import { createWorkbenchBridge } from './workbench-bridge'

describe('workbench bridge', () => {
  it('accepts only the active iframe and exact managed origin', async () => {
    const invoke = vi.fn().mockResolvedValue({ ok: true })
    const postMessage = vi.fn()
    const contentWindow = { postMessage } as unknown as Window
    const frame = { contentWindow } as HTMLIFrameElement
    const bridge = createWorkbenchBridge({
      frame: () => frame,
      active: () => ({ generationId: 'g-2', origin: 'http://127.0.0.1:39000' }),
      invoke,
    })
    const request = {
      channel: DESKTOP_BRIDGE_CHANNEL,
      requestId: 'r-1',
      action: 'profile.list',
      payload: {},
    }

    await bridge.onMessage({ source: contentWindow, origin: 'http://127.0.0.1:39000', data: request } as MessageEvent)
    expect(invoke).toHaveBeenCalledWith('list_profiles', { generationId: 'g-2' })
    expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({ requestId: 'r-1', ok: true }), 'http://127.0.0.1:39000')

    await bridge.onMessage({ source: contentWindow, origin: 'http://127.0.0.1:39001', data: { ...request, requestId: 'r-2' } } as MessageEvent)
    await bridge.onMessage({ source: {}, origin: 'http://127.0.0.1:39000', data: { ...request, requestId: 'r-3' } } as MessageEvent)
    expect(invoke).toHaveBeenCalledTimes(1)
  })

  it('rejects unknown actions, malformed ids, oversized messages, and generation overrides', async () => {
    const invoke = vi.fn().mockResolvedValue(undefined)
    const contentWindow = { postMessage: vi.fn() } as unknown as Window
    const bridge = createWorkbenchBridge({
      frame: () => ({ contentWindow }) as HTMLIFrameElement,
      active: () => ({ generationId: 'g-active', origin: 'http://127.0.0.1:39000' }),
      invoke,
    })
    const send = (data: unknown) => bridge.onMessage({ source: contentWindow, origin: 'http://127.0.0.1:39000', data } as MessageEvent)

    await send({ channel: DESKTOP_BRIDGE_CHANNEL, requestId: '../bad', action: 'profile.list', payload: {} })
    await send({ channel: DESKTOP_BRIDGE_CHANNEL, requestId: 'r-2', action: 'shell.execute', payload: {} })
    await send({ channel: DESKTOP_BRIDGE_CHANNEL, requestId: 'r-3', action: 'profile.list', payload: { value: 'x'.repeat(33 * 1024) } })
    await send({ channel: DESKTOP_BRIDGE_CHANNEL, requestId: 'r-4', action: 'profile.delete', payload: { generationId: 'g-stale', profileId: 'p-1' } })

    expect(invoke).toHaveBeenCalledTimes(1)
    expect(invoke).toHaveBeenCalledWith('delete_profile', { generationId: 'g-active', profileId: 'p-1' })
  })

  it('returns a sanitized correlated error', async () => {
    const postMessage = vi.fn()
    const contentWindow = { postMessage } as unknown as Window
    const bridge = createWorkbenchBridge({
      frame: () => ({ contentWindow }) as HTMLIFrameElement,
      active: () => ({ generationId: 'g-1', origin: 'http://127.0.0.1:39000' }),
      invoke: vi.fn().mockRejectedValue({ code: 'conflict', message: 'revision changed', secret: 'hidden' }),
    })
    await bridge.onMessage({
      source: contentWindow,
      origin: 'http://127.0.0.1:39000',
      data: { channel: DESKTOP_BRIDGE_CHANNEL, requestId: 'r-5', action: 'profile.list', payload: {} },
    } as MessageEvent)
    expect(postMessage).toHaveBeenCalledWith({
      channel: DESKTOP_BRIDGE_CHANNEL,
      requestId: 'r-5',
      ok: false,
      error: { code: 'conflict', message: 'revision changed' },
    }, 'http://127.0.0.1:39000')
  })

  it('maps project metadata actions and forces the active generation', async () => {
    const invoke = vi.fn().mockResolvedValue({ schemaVersion: 1, projects: {} })
    const postMessage = vi.fn()
    const contentWindow = { postMessage } as unknown as Window
    const bridge = createWorkbenchBridge({
      frame: () => ({ contentWindow }) as HTMLIFrameElement,
      active: () => ({ generationId: 'g-active', origin: 'http://127.0.0.1:39000' }),
      invoke,
    })
    await bridge.onMessage({
      source: contentWindow,
      origin: 'http://127.0.0.1:39000',
      data: {
        channel: DESKTOP_BRIDGE_CHANNEL,
        requestId: 'r-project',
        action: 'project.metadata.patch',
        payload: { generationId: 'g-stale', workspaceId: 'w-1', patch: { pinned: true } },
      },
    } as MessageEvent)

    expect(invoke).toHaveBeenCalledWith('patch_project_metadata', {
      generationId: 'g-active',
      workspaceId: 'w-1',
      patch: { pinned: true },
    })
    expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({ requestId: 'r-project', ok: true }), 'http://127.0.0.1:39000')
  })

  it('maps project recycling without accepting a renderer path', async () => {
    const invoke = vi.fn().mockResolvedValue('C:\\code\\demo')
    const contentWindow = { postMessage: vi.fn() } as unknown as Window
    const bridge = createWorkbenchBridge({
      frame: () => ({ contentWindow }) as HTMLIFrameElement,
      active: () => ({ generationId: 'g-active', origin: 'http://127.0.0.1:39000' }),
      invoke,
    })
    await bridge.onMessage({
      source: contentWindow,
      origin: 'http://127.0.0.1:39000',
      data: {
        channel: DESKTOP_BRIDGE_CHANNEL,
        requestId: 'r-recycle',
        action: 'project.directory.recycle',
        payload: { workspaceId: 'w-1', path: 'C:\\Windows' },
      },
    } as MessageEvent)

    expect(invoke).toHaveBeenCalledWith('recycle_project_directory', {
      generationId: 'g-active',
      workspaceId: 'w-1',
    })
  })

  it('previews and creates only backend-owned default project locations', async () => {
    const invoke = vi.fn()
      .mockResolvedValueOnce({
        projectName: '记账应用',
        suggestedPath: 'C:\\Users\\test\\Documents\\DeepSeek Harness\\Projects\\记账应用',
      })
      .mockResolvedValueOnce('C:\\Users\\test\\Documents\\DeepSeek Harness\\Projects\\记账应用')
    const contentWindow = { postMessage: vi.fn() } as unknown as Window
    const bridge = createWorkbenchBridge({
      frame: () => ({ contentWindow }) as HTMLIFrameElement,
      active: () => ({ generationId: 'g-active', origin: 'http://127.0.0.1:39000' }),
      invoke,
    })

    const send = (requestId: string, action: string, payload: unknown) => bridge.onMessage({
      source: contentWindow,
      origin: 'http://127.0.0.1:39000',
      data: {
        channel: DESKTOP_BRIDGE_CHANNEL,
        requestId,
        action,
        payload,
      },
    } as MessageEvent)

    await send('r-preview', 'project.directory.preview', {
      idea: '做一个记账应用',
      path: 'C:\\Windows',
      generationId: 'g-stale',
    })
    await send('r-create', 'project.directory.create', {
      projectName: '记账应用',
      path: 'C:\\Windows',
      generationId: 'g-stale',
    })

    expect(invoke).toHaveBeenNthCalledWith(1, 'preview_default_project_directory', {
      generationId: 'g-active',
      idea: '做一个记账应用',
    })
    expect(invoke).toHaveBeenNthCalledWith(2, 'create_default_project_directory', {
      generationId: 'g-active',
      projectName: '记账应用',
    })
  })

  it('forwards app actions with a validated workspaceId', async () => {
    const invoke = vi.fn()
      .mockResolvedValueOnce({ workspaceId: 'w-1', origin: 'http://127.0.0.1:39123', title: 'demo' })
      .mockResolvedValueOnce({ projectsRoot: 'C:\\Projects', running: [], launchable: ['w-1'] })
    const postMessage = vi.fn()
    const contentWindow = { postMessage } as unknown as Window
    const bridge = createWorkbenchBridge({
      frame: () => ({ contentWindow }) as HTMLIFrameElement,
      active: () => ({ generationId: 'g-active', origin: 'http://127.0.0.1:39000' }),
      invoke,
    })

    const send = (requestId: string, action: string, payload: unknown) => bridge.onMessage({
      source: contentWindow,
      origin: 'http://127.0.0.1:39000',
      data: {
        channel: DESKTOP_BRIDGE_CHANNEL,
        requestId,
        action,
        payload,
      },
    } as MessageEvent)

    await send('r-launch', 'app.launch', { workspaceId: 'w-1' })
    await send('r-status', 'app.status', {})
    await send('r-stop', 'app.stop', { workspaceId: '  ' })

    expect(invoke).toHaveBeenNthCalledWith(1, 'app_launch', { workspaceId: 'w-1', generationId: 'g-active' })
    expect(postMessage).toHaveBeenNthCalledWith(1, expect.objectContaining({
      requestId: 'r-launch',
      ok: true,
      result: { workspaceId: 'w-1', origin: 'http://127.0.0.1:39123', title: 'demo' },
    }), 'http://127.0.0.1:39000')

    expect(invoke).toHaveBeenNthCalledWith(2, 'app_status', { generationId: 'g-active' })
    expect(postMessage).toHaveBeenNthCalledWith(2, expect.objectContaining({
      requestId: 'r-status',
      ok: true,
      result: { projectsRoot: 'C:\\Projects', running: [], launchable: ['w-1'] },
    }), 'http://127.0.0.1:39000')

    expect(invoke).toHaveBeenCalledTimes(2)
    expect(postMessage).toHaveBeenNthCalledWith(3, expect.objectContaining({
      requestId: 'r-stop',
      ok: false,
      error: expect.objectContaining({ code: expect.any(String), message: expect.any(String) }),
    }), 'http://127.0.0.1:39000')
  })

  it('accepts only the active generation and session for v2 task requests', async () => {
    const invoke = vi.fn().mockResolvedValue({ taskId: 'task-1' })
    const postMessage = vi.fn()
    const contentWindow = { postMessage } as unknown as Window
    const bridge = createWorkbenchBridge({
      frame: () => ({ contentWindow }) as HTMLIFrameElement,
      active: () => ({
        generationId: 'generation-1',
        sessionId: 'session-1',
        origin: 'http://127.0.0.1:39000',
      }),
      invoke,
    })
    await bridge.onMessage({
      source: contentWindow,
      origin: 'http://127.0.0.1:39000',
      data: {
        channel: DESKTOP_BRIDGE_V2_CHANNEL,
        requestId: 'request-1',
        generationId: 'generation-stale',
        sessionId: 'session-1',
        action: 'task.create',
        payload: { workspaceId: 'workspace-1', prompt: '检查项目', permission: 'request-approval' },
      },
    } as MessageEvent)
    await bridge.onMessage({
      source: contentWindow,
      origin: 'http://127.0.0.1:39000',
      data: {
        channel: DESKTOP_BRIDGE_V2_CHANNEL,
        requestId: 'request-2',
        generationId: 'generation-1',
        sessionId: 'session-2',
        action: 'task.create',
        payload: { workspaceId: 'workspace-1', prompt: '检查项目', permission: 'request-approval' },
      },
    } as MessageEvent)
    await bridge.onMessage({
      source: contentWindow,
      origin: 'http://127.0.0.1:39000',
      data: {
        channel: DESKTOP_BRIDGE_V2_CHANNEL,
        requestId: 'request-3',
        generationId: 'generation-1',
        sessionId: 'session-1',
        action: 'task.create',
        payload: { workspaceId: 'workspace-1', prompt: '检查项目', permission: 'request-approval' },
      },
    } as MessageEvent)
    expect(invoke).toHaveBeenCalledTimes(1)
    expect(invoke).toHaveBeenCalledWith('agent_task_create', {
      generationId: 'generation-1',
      sessionId: 'session-1',
      workspaceId: 'workspace-1',
      prompt: '检查项目',
      permission: 'request-approval',
    })
    expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({
      channel: DESKTOP_BRIDGE_V2_CHANNEL,
      requestId: 'request-3',
      ok: true,
    }), 'http://127.0.0.1:39000')
  })

  it('forwards prompts payloads with whitelisted fields for v2 requests', async () => {
    const invoke = vi.fn().mockResolvedValue({ ok: true })
    const postMessage = vi.fn()
    const contentWindow = { postMessage } as unknown as Window
    const bridge = createWorkbenchBridge({
      frame: () => ({ contentWindow }) as HTMLIFrameElement,
      active: () => ({
        generationId: 'generation-1',
        sessionId: 'session-1',
        origin: 'http://127.0.0.1:39000',
      }),
      invoke,
    })
    const send = (requestId: string, action: string, payload: unknown) => bridge.onMessage({
      source: contentWindow,
      origin: 'http://127.0.0.1:39000',
      data: {
        channel: DESKTOP_BRIDGE_V2_CHANNEL,
        requestId,
        generationId: 'generation-1',
        sessionId: 'session-1',
        action,
        payload,
      },
    } as MessageEvent)

    await send('request-prompts-save', 'prompts.save', { presetId: 'preset-1', title: '审查提示词', content: '请审查代码' })
    await send('request-prompts-save-new', 'prompts.save', { title: '新提示词', content: '内容' })
    await send('request-prompts-activate', 'prompts.activate', { presetId: 'preset-1', target: 'claude' })
    await send('request-prompts-status', 'prompts.status', {})

    expect(invoke).toHaveBeenNthCalledWith(1, 'prompts_save', {
      generationId: 'generation-1',
      sessionId: 'session-1',
      presetId: 'preset-1',
      title: '审查提示词',
      content: '请审查代码',
    })
    expect(invoke).toHaveBeenNthCalledWith(2, 'prompts_save', {
      generationId: 'generation-1',
      sessionId: 'session-1',
      title: '新提示词',
      content: '内容',
    })
    expect(invoke).toHaveBeenNthCalledWith(3, 'prompts_activate', {
      generationId: 'generation-1',
      sessionId: 'session-1',
      presetId: 'preset-1',
      target: 'claude',
    })
    expect(invoke).toHaveBeenNthCalledWith(4, 'prompts_status', {
      generationId: 'generation-1',
      sessionId: 'session-1',
    })
    expect(postMessage).toHaveBeenCalledTimes(4)
    expect(postMessage).toHaveBeenNthCalledWith(4, expect.objectContaining({
      channel: DESKTOP_BRIDGE_V2_CHANNEL,
      requestId: 'request-prompts-status',
      ok: true,
    }), 'http://127.0.0.1:39000')
  })

  it('forwards MCP management actions to mcp_manager commands', async () => {
    const invoke = vi.fn().mockResolvedValue([])
    const postMessage = vi.fn()
    const contentWindow = { postMessage } as unknown as Window
    const bridge = createWorkbenchBridge({
      frame: () => ({ contentWindow }) as HTMLIFrameElement,
      active: () => ({ generationId: 'generation-1', sessionId: 'session-1', origin: 'http://127.0.0.1:39000' }),
      invoke,
    })
    const send = (requestId: string, action: string, payload: unknown) => bridge.onMessage({
      source: contentWindow,
      origin: 'http://127.0.0.1:39000',
      data: {
        channel: DESKTOP_BRIDGE_V2_CHANNEL,
        requestId,
        generationId: 'generation-1',
        sessionId: 'session-1',
        action,
        payload,
      },
    } as MessageEvent)

    await send('request-mcp-list', 'mcp.list', {})
    await send('request-mcp-upsert', 'mcp.upsert', { id: 'srv-1', name: 'fetch', command: 'npx', args: ['-y'], env: { NO_PROXY: '127.0.0.1' }, targets: ['claude'] })
    await send('request-mcp-upsert-new', 'mcp.upsert', { name: 'fetch', command: 'npx', targets: ['codex'] })
    await send('request-mcp-delete', 'mcp.delete', { id: 'srv-1' })
    await send('request-mcp-sync', 'mcp.sync', { target: 'claude' })
    await send('request-mcp-import', 'mcp.import', { target: 'codex' })
    await send('request-mcp-status', 'mcp.status', {})

    expect(invoke).toHaveBeenNthCalledWith(1, 'mcp_manager_list', { generationId: 'generation-1', sessionId: 'session-1' })
    expect(invoke).toHaveBeenNthCalledWith(2, 'mcp_manager_upsert', {
      generationId: 'generation-1',
      sessionId: 'session-1',
      id: 'srv-1',
      name: 'fetch',
      command: 'npx',
      args: ['-y'],
      env: { NO_PROXY: '127.0.0.1' },
      targets: ['claude'],
    })
    expect(invoke).toHaveBeenNthCalledWith(3, 'mcp_manager_upsert', {
      generationId: 'generation-1',
      sessionId: 'session-1',
      name: 'fetch',
      command: 'npx',
      args: [],
      env: {},
      targets: ['codex'],
    })
    expect(invoke).toHaveBeenNthCalledWith(4, 'mcp_manager_delete', { generationId: 'generation-1', sessionId: 'session-1', id: 'srv-1' })
    expect(invoke).toHaveBeenNthCalledWith(5, 'mcp_manager_sync', { generationId: 'generation-1', sessionId: 'session-1', target: 'claude' })
    expect(invoke).toHaveBeenNthCalledWith(6, 'mcp_manager_import', { generationId: 'generation-1', sessionId: 'session-1', target: 'codex' })
    expect(invoke).toHaveBeenNthCalledWith(7, 'mcp_manager_status', { generationId: 'generation-1', sessionId: 'session-1' })
    expect(postMessage).toHaveBeenCalledTimes(7)
  })

  it('lets MCP definition results with user-authored env values reach the panel', async () => {
    const invoke = vi.fn().mockResolvedValue([
      { id: 'srv-1', name: 'fetch', command: 'npx', args: [], env: { API_KEY: 'user-entered' }, targets: ['claude'] },
    ])
    const postMessage = vi.fn()
    const contentWindow = { postMessage } as unknown as Window
    const bridge = createWorkbenchBridge({
      frame: () => ({ contentWindow }) as HTMLIFrameElement,
      active: () => ({ generationId: 'generation-1', sessionId: 'session-1', origin: 'http://127.0.0.1:39000' }),
      invoke,
    })
    await bridge.onMessage({
      source: contentWindow,
      origin: 'http://127.0.0.1:39000',
      data: {
        channel: DESKTOP_BRIDGE_V2_CHANNEL,
        requestId: 'request-mcp-list',
        generationId: 'generation-1',
        sessionId: 'session-1',
        action: 'mcp.list',
        payload: {},
      },
    } as MessageEvent)
    const response = postMessage.mock.calls[0]?.[0] as { ok?: boolean; result?: unknown }
    expect(response.ok).toBe(true)
    expect(response.result).toEqual([
      { id: 'srv-1', name: 'fetch', command: 'npx', args: [], env: { API_KEY: 'user-entered' }, targets: ['claude'] },
    ])
  })

  it('forwards Harness connection maintenance actions with provider ownership', async () => {
    const invoke = vi.fn().mockResolvedValue({ profileId: 'yunxiao-readonly' })
    const contentWindow = { postMessage: vi.fn() } as unknown as Window
    const bridge = createWorkbenchBridge({
      frame: () => ({ contentWindow }) as HTMLIFrameElement,
      active: () => ({ generationId: 'generation-1', sessionId: 'session-1', origin: 'http://127.0.0.1:39000' }),
      invoke,
    })
    await bridge.onMessage({
      source: contentWindow,
      origin: 'http://127.0.0.1:39000',
      data: {
        channel: DESKTOP_BRIDGE_V2_CHANNEL,
        requestId: 'request-profile',
        generationId: 'generation-1',
        sessionId: 'session-1',
        action: 'harness.connection.save',
        payload: {
          kind: 'mcp', providerId: 'yunxiao', displayName: '云效', endpoint: 'https://example.test',
          readOnly: true, enabled: true,
        },
      },
    } as MessageEvent)
    expect(invoke).toHaveBeenCalledWith('harness_connection_save', expect.objectContaining({
      generationId: 'generation-1', sessionId: 'session-1', kind: 'mcp', providerId: 'yunxiao', readOnly: true,
    }))
  })

  it('forwards only bounded generalized connection fields to the native host', async () => {
    const invoke = vi.fn().mockResolvedValue({ profileId: 'project-memory' })
    const contentWindow = { postMessage: vi.fn() } as unknown as Window
    const bridge = createWorkbenchBridge({
      frame: () => ({ contentWindow }) as HTMLIFrameElement,
      active: () => ({ generationId: 'generation-1', sessionId: 'session-1', origin: 'http://127.0.0.1:39000' }),
      invoke,
    })
    await bridge.onMessage({
      source: contentWindow,
      origin: 'http://127.0.0.1:39000',
      data: {
        channel: DESKTOP_BRIDGE_V2_CHANNEL,
        requestId: 'request-custom-profile',
        generationId: 'generation-1',
        sessionId: 'session-1',
        action: 'harness.connection.save',
        payload: {
          kind: 'mcp', transport: 'stdio', templateId: 'custom', displayName: '项目记忆',
          endpoint: '', command: 'node', args: ['server.js'], environmentKeys: ['PROJECT_ROOT'],
          workingDirectoryPolicy: 'workspace', healthPath: '', readOnly: true, enabled: true,
        },
      },
    } as MessageEvent)
    expect(invoke).toHaveBeenCalledWith('harness_connection_save', expect.objectContaining({
      generationId: 'generation-1', sessionId: 'session-1', kind: 'mcp', transport: 'stdio',
      templateId: 'custom', command: 'node', args: ['server.js'], environmentKeys: ['PROJECT_ROOT'],
      workingDirectoryPolicy: 'workspace',
    }))
  })

  it('forwards structured database fields and local skill actions without renderer-owned paths', async () => {
    const invoke = vi.fn().mockResolvedValue({ extensionId: 'skill.code-review' })
    const contentWindow = { postMessage: vi.fn() } as unknown as Window
    const bridge = createWorkbenchBridge({
      frame: () => ({ contentWindow }) as HTMLIFrameElement,
      active: () => ({ generationId: 'generation-1', sessionId: 'session-1', origin: 'http://127.0.0.1:39000' }),
      invoke,
    })
    const send = (requestId: string, action: string, payload: unknown) => bridge.onMessage({
      source: contentWindow,
      origin: 'http://127.0.0.1:39000',
      data: { channel: DESKTOP_BRIDGE_V2_CHANNEL, requestId, generationId: 'generation-1', sessionId: 'session-1', action, payload },
    } as MessageEvent)

    await send('request-database', 'harness.connection.save', {
      kind: 'database', transport: 'database', templateId: 'custom', providerId: 'generic', displayName: 'HIS 只读库',
      endpoint: 'postgresql://db.internal:5432/his', databaseType: 'postgresql', host: 'db.internal', port: 5432,
      databaseName: 'his', username: 'readonly_user', encoding: 'UTF-8', testQuery: 'SELECT 1', readOnly: true, enabled: true,
    })
    await send('request-skill-create', 'skill.create', {
      skillId: 'code-review', displayName: '代码审查', description: '', instructions: '检查代码。',
    })
    await send('request-skill-import', 'skill.import', {})

    expect(invoke).toHaveBeenNthCalledWith(1, 'harness_connection_save', expect.objectContaining({
      databaseType: 'postgresql', host: 'db.internal', port: 5432, databaseName: 'his', username: 'readonly_user',
    }))
    expect(invoke).toHaveBeenNthCalledWith(2, 'agent_skill_create', expect.objectContaining({ skillId: 'code-review' }))
    expect(invoke).toHaveBeenNthCalledWith(3, 'agent_skill_import', {
      generationId: 'generation-1', sessionId: 'session-1',
    })
  })

  it('forwards the intake model selection and the native archive-root picker', async () => {
    const invoke = vi.fn().mockResolvedValue({ state: 'running' })
    const contentWindow = { postMessage: vi.fn() } as unknown as Window
    const bridge = createWorkbenchBridge({
      frame: () => ({ contentWindow }) as HTMLIFrameElement,
      active: () => ({ generationId: 'generation-1', sessionId: 'session-1', origin: 'http://127.0.0.1:39000' }),
      invoke,
    })
    await bridge.onMessage({
      source: contentWindow,
      origin: 'http://127.0.0.1:39000',
      data: {
        channel: DESKTOP_BRIDGE_V2_CHANNEL,
        requestId: 'request-intake',
        generationId: 'generation-1',
        sessionId: 'session-1',
        action: 'harness.intake',
        payload: {
          source: 'DFHIS-39999',
          archiveRoot: '/Users/test/harness-archives',
          selectedModelId: 'deepseek-reasoner',
          agentBackend: 'deepseek',
        },
      },
    } as MessageEvent)
    expect(invoke).toHaveBeenCalledWith('harness_intake', expect.objectContaining({
      generationId: 'generation-1', sessionId: 'session-1',
      source: 'DFHIS-39999', selectedModelId: 'deepseek-reasoner', agentBackend: 'deepseek',
    }))
    await bridge.onMessage({
      source: contentWindow,
      origin: 'http://127.0.0.1:39000',
      data: {
        channel: DESKTOP_BRIDGE_V2_CHANNEL,
        requestId: 'request-pick',
        generationId: 'generation-1',
        sessionId: 'session-1',
        action: 'harness.pick-archive-root',
        payload: {},
      },
    } as MessageEvent)
    expect(invoke).toHaveBeenCalledWith('harness_pick_archive_root', {
      generationId: 'generation-1',
      sessionId: 'session-1',
    })
  })

  it('forwards validated agent events only to the active iframe session', () => {
    const postMessage = vi.fn()
    const contentWindow = { postMessage } as unknown as Window
    const bridge = createWorkbenchBridge({
      frame: () => ({ contentWindow }) as HTMLIFrameElement,
      active: () => ({
        generationId: 'generation-1',
        sessionId: 'session-1',
        origin: 'http://127.0.0.1:39000',
      }),
      invoke: vi.fn(),
    })
    bridge.onAgentEvent({
      channel: AGENT_EVENT_CHANNEL,
      generationId: 'generation-1',
      taskId: 'task-1',
      sessionId: 'session-1',
      sequence: 1,
      type: 'task.progress',
      payload: { percent: 20 },
    })
    bridge.onAgentEvent({
      channel: AGENT_EVENT_CHANNEL,
      generationId: 'generation-stale',
      taskId: 'task-1',
      sessionId: 'session-1',
      sequence: 2,
      type: 'task.progress',
      payload: { percent: 40 },
    })
    expect(postMessage).toHaveBeenCalledTimes(1)
    expect(postMessage).toHaveBeenCalledWith(expect.objectContaining({
      taskId: 'task-1',
      sequence: 1,
    }), 'http://127.0.0.1:39000')
  })
})
