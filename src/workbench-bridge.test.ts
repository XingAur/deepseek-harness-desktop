import { describe, expect, it, vi } from 'vitest'
import { DESKTOP_BRIDGE_CHANNEL } from './bridge-contract'
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

  it('maps project directory creation through the active generation', async () => {
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
        requestId: 'r-create-directory',
        action: 'project.directory.create',
        payload: { path: 'C:\\code\\demo', generationId: 'g-stale' },
      },
    } as MessageEvent)

    expect(invoke).toHaveBeenCalledWith('create_project_directory_command', {
      generationId: 'g-active',
      path: 'C:\\code\\demo',
    })
  })
})
