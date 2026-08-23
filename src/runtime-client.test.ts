import { beforeEach, describe, expect, it, vi } from 'vitest'

const tauri = vi.hoisted(() => ({
  invoke: vi.fn(),
  listen: vi.fn(),
}))

vi.mock('@tauri-apps/api/core', () => ({ invoke: tauri.invoke }))
vi.mock('@tauri-apps/api/event', () => ({ listen: tauri.listen }))

import { tauriRuntimeClient } from './runtime-client'

describe('runtime client app updates', () => {
  beforeEach(() => {
    tauri.invoke.mockReset().mockResolvedValue({ phase: 'idle' })
    tauri.listen.mockReset()
  })

  it('passes the explicit update source to the native command', async () => {
    await tauriRuntimeClient.checkAppUpdate('automatic')
    expect(tauri.invoke).toHaveBeenCalledWith('check_app_update', { source: 'automatic' })
  })

  it('opens only the native controller stored update download', async () => {
    await tauriRuntimeClient.openAppUpdateDownload()
    expect(tauri.invoke).toHaveBeenCalledWith('open_app_update_download')
  })

  it('forwards source-aware native update events', async () => {
    let receive: ((event: { payload: unknown }) => void) | undefined
    tauri.listen.mockImplementation(async (_name, listener) => {
      receive = listener
      return vi.fn()
    })
    const listener = vi.fn()
    await tauriRuntimeClient.subscribeAppUpdates(listener)
    receive?.({ payload: {
      source: 'manual',
      state: { phase: 'failed', update: { code: 'check', message: 'offline', recoverable: true } },
    } })
    expect(listener).toHaveBeenCalledWith(expect.objectContaining({ source: 'manual' }))
  })

  it('subscribes to local-app-event channel and forwards payloads', async () => {
    let receive: ((event: { payload: unknown }) => void) | undefined
    tauri.listen.mockImplementation(async (_name, listener) => {
      receive = listener
      return vi.fn()
    })
    const listener = vi.fn()
    await tauriRuntimeClient.subscribeLocalAppEvents(listener)
    receive?.({ payload: {
      kind: 'launched',
      workspaceId: 'w-1',
      origin: 'http://127.0.0.1:39123',
      title: 'demo'
    } })
    expect(listener).toHaveBeenCalledWith({
      kind: 'launched',
      workspaceId: 'w-1',
      origin: 'http://127.0.0.1:39123',
      title: 'demo'
    })
    expect(tauri.listen).toHaveBeenCalledWith('local-app-event', expect.any(Function))
  })
})
