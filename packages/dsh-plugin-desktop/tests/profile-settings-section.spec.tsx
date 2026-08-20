import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ProfileSettingsSection } from '../src/client/ProfileSettingsSection'
import { bridgeFixture } from './fixtures'

const snapshot = {
  selectedProfileId: 'p-a',
  pendingProfileId: null,
  lastKnownGoodProfileId: 'p-a',
  profiles: [
    { id: 'p-a', name: '默认', dataRoot: 'C:\\data\\a', permissionMode: 'workspace-write' as const, revision: 2, runtimeVersion: '1.8.2', status: 'active' as const },
    { id: 'p-b', name: '只读', dataRoot: 'C:\\data\\b', permissionMode: 'read-only' as const, revision: 1, status: 'ready' as const },
  ],
}

describe('profile settings section', () => {
  it('creates a profile and protects active and last-known-good profiles from deletion', async () => {
    const bridge = bridgeFixture({ 'profile.list': snapshot })
    render(<ProfileSettingsSection bridge={bridge} />)

    fireEvent.click(await screen.findByRole('button', { name: '新建 Profile' }))
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '隔离测试' } })
    fireEvent.change(screen.getByLabelText('数据目录'), { target: { value: 'C:\\data\\test' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))

    await waitFor(() => expect(bridge.request).toHaveBeenCalledWith('profile.create', {
      draft: { name: '隔离测试', dataRoot: 'C:\\data\\test', permissionMode: 'workspace-write' },
    }))
    expect(screen.getByRole('button', { name: '删除 默认' })).toBeDisabled()
  })

  it('updates and duplicates profiles with typed bridge payloads', async () => {
    const bridge = bridgeFixture({ 'profile.list': snapshot })
    render(<ProfileSettingsSection bridge={bridge} />)

    fireEvent.click(await screen.findByRole('button', { name: '编辑 只读' }))
    fireEvent.change(screen.getByLabelText('名称'), { target: { value: '只读修订' } })
    fireEvent.change(screen.getByLabelText('权限模式'), { target: { value: 'workspace-write' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(bridge.request).toHaveBeenCalledWith('profile.update', {
      profileId: 'p-b', expectedRevision: 1,
      patch: { name: '只读修订', dataRoot: 'C:\\data\\b', permissionMode: 'workspace-write' },
    }))

    fireEvent.click(screen.getByRole('button', { name: '复制 只读' }))
    await waitFor(() => expect(bridge.request).toHaveBeenCalledWith('profile.duplicate', {
      profileId: 'p-b',
      draft: { name: '只读 副本', dataRoot: 'C:\\data\\b-copy', permissionMode: 'read-only' },
    }))
  })

  it('surfaces mutation failures and restores usable controls', async () => {
    const bridge = bridgeFixture({ 'profile.list': snapshot })
    vi.mocked(bridge.request).mockImplementation(async (action) => {
      if (action === 'profile.list') return snapshot
      throw new Error('Profile revision 已变化，请刷新后重试')
    })
    render(<ProfileSettingsSection bridge={bridge} />)

    fireEvent.click(await screen.findByRole('button', { name: '删除 只读' }))

    expect(await screen.findByRole('alert')).toHaveTextContent('Profile revision 已变化')
    expect(screen.getByRole('button', { name: '删除 只读' })).toBeEnabled()
  })
})
