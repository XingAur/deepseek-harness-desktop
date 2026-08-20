import { describe, expect, it, vi } from 'vitest'
import { createDesktopBridge, DESKTOP_BRIDGE_CHANNEL } from '../src/client/desktop-bridge'

function fixture(timeoutMs = 100) {
  const listeners = new Set<(event: MessageEvent) => void>()
  const host = {
    addEventListener: (_type: 'message', listener: (event: MessageEvent) => void) => listeners.add(listener),
    removeEventListener: (_type: 'message', listener: (event: MessageEvent) => void) => listeners.delete(listener),
  }
  const parent = { postMessage: vi.fn() }
  const bridge = createDesktopBridge({ host, parent, targetOrigin: 'tauri://localhost', timeoutMs, createRequestId: () => 'r-1' })
  return { bridge, parent, emit: (event: MessageEvent) => listeners.forEach((listener) => listener(event)), listeners }
}

describe('desktop bridge client', () => {
  it('correlates replies from only the configured parent and origin', async () => {
    const { bridge, parent, emit } = fixture()
    const pending = bridge.request('profile.list')
    expect(parent.postMessage).toHaveBeenCalledWith({ channel: DESKTOP_BRIDGE_CHANNEL, requestId: 'r-1', action: 'profile.list', payload: {} }, 'tauri://localhost')
    emit({ source: {}, origin: 'tauri://localhost', data: { channel: DESKTOP_BRIDGE_CHANNEL, requestId: 'r-1', ok: true, result: ['wrong'] } } as unknown as MessageEvent)
    emit({ source: parent, origin: 'https://evil.example', data: { channel: DESKTOP_BRIDGE_CHANNEL, requestId: 'r-1', ok: true, result: ['wrong'] } } as unknown as MessageEvent)
    emit({ source: parent, origin: 'tauri://localhost', data: { channel: DESKTOP_BRIDGE_CHANNEL, requestId: 'r-1', ok: true, result: ['ok'] } } as unknown as MessageEvent)
    await expect(pending).resolves.toEqual(['ok'])
  })

  it('rejects timed out and disposed requests', async () => {
    vi.useFakeTimers()
    const timed = fixture(15)
    const timedRequest = timed.bridge.request('profile.list')
    const timedAssertion = expect(timedRequest).rejects.toThrow('超时')
    await vi.advanceTimersByTimeAsync(16)
    await timedAssertion

    const disposed = fixture(100)
    const disposedRequest = disposed.bridge.request('diagnostics.export')
    const disposedAssertion = expect(disposedRequest).rejects.toThrow('已关闭')
    disposed.bridge.dispose()
    await disposedAssertion
    expect(disposed.listeners.size).toBe(0)
    vi.useRealTimers()
  })

  it('rejects correlated errors', async () => {
    const { bridge, parent, emit } = fixture()
    const pending = bridge.request('profile.switch', { profileId: 'p-1' })
    emit({ source: parent, origin: 'tauri://localhost', data: { channel: DESKTOP_BRIDGE_CHANNEL, requestId: 'r-1', ok: false, error: { code: 'conflict', message: '请刷新' } } } as unknown as MessageEvent)
    await expect(pending).rejects.toThrow('请刷新')
  })

  it('sends project metadata actions through the managed channel', async () => {
    const { bridge, parent, emit } = fixture()
    const pending = bridge.request('project.metadata.patch', { workspaceId: 'w-1', patch: { cover: 'forest' } })
    expect(parent.postMessage).toHaveBeenCalledWith({
      channel: DESKTOP_BRIDGE_CHANNEL,
      requestId: 'r-1',
      action: 'project.metadata.patch',
      payload: { workspaceId: 'w-1', patch: { cover: 'forest' } },
    }, 'tauri://localhost')
    emit({
      source: parent,
      origin: 'tauri://localhost',
      data: { channel: DESKTOP_BRIDGE_CHANNEL, requestId: 'r-1', ok: true, result: { schemaVersion: 1, projects: {} } },
    } as unknown as MessageEvent)
    await expect(pending).resolves.toEqual({ schemaVersion: 1, projects: {} })
  })

  it('sends only a workspace id when recycling a registered project', async () => {
    const { bridge, parent, emit } = fixture()
    const pending = bridge.request('project.directory.recycle', { workspaceId: 'w-1' })
    expect(parent.postMessage).toHaveBeenCalledWith({
      channel: DESKTOP_BRIDGE_CHANNEL,
      requestId: 'r-1',
      action: 'project.directory.recycle',
      payload: { workspaceId: 'w-1' },
    }, 'tauri://localhost')
    emit({ source: parent, origin: 'tauri://localhost', data: {
      channel: DESKTOP_BRIDGE_CHANNEL, requestId: 'r-1', ok: true, result: 'C:\\code\\demo',
    } } as unknown as MessageEvent)
    await expect(pending).resolves.toBe('C:\\code\\demo')
  })
})
