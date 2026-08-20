import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ProfileSelector } from '../src/client/ProfileSelector'
import { bridgeFixture } from './fixtures'

describe('profile selector', () => {
  it('shows one profile as a compact status without a duplicate dropdown', async () => {
    const bridge = bridgeFixture({
      'profile.list': {
        selectedProfileId: 'p-a', pendingProfileId: null, lastKnownGoodProfileId: 'p-a',
        profiles: [{ id: 'p-a', name: '默认', status: 'active', revision: 1, runtimeVersion: '0.1.0-preview' }],
      },
    })

    render(<ProfileSelector bridge={bridge} />)

    expect(await screen.findByLabelText('当前 Profile：默认')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Profile：默认' })).not.toBeInTheDocument()
    expect(screen.queryByRole('listbox', { name: '选择 Profile' })).not.toBeInTheDocument()
  })

  it('switches profile through the desktop bridge and locks while the new generation starts', async () => {
    const bridge = bridgeFixture({
      'profile.list': {
        selectedProfileId: 'p-a', pendingProfileId: null, lastKnownGoodProfileId: 'p-a',
        profiles: [
          { id: 'p-a', name: 'A', status: 'active', revision: 1, runtimeVersion: '1.8.2' },
          { id: 'p-b', name: 'B', status: 'ready', revision: 1 },
        ],
      },
    })
    let finishSwitch: (() => void) | undefined
    vi.mocked(bridge.request).mockImplementation(async (action) => {
      if (action === 'profile.list') return {
        selectedProfileId: 'p-a', pendingProfileId: null, lastKnownGoodProfileId: 'p-a',
        profiles: [
          { id: 'p-a', name: 'A', status: 'active', revision: 1, runtimeVersion: '1.8.2' },
          { id: 'p-b', name: 'B', status: 'ready', revision: 1 },
        ],
      }
      return new Promise<void>((resolve) => { finishSwitch = resolve })
    })
    render(<ProfileSelector bridge={bridge} />)

    const trigger = await screen.findByRole('button', { name: 'Profile：A' })
    fireEvent.keyDown(trigger, { key: 'ArrowDown' })
    const listbox = screen.getByRole('listbox', { name: '选择 Profile' })
    expect(listbox).toBeVisible()
    fireEvent.keyDown(listbox, { key: 'End' })
    fireEvent.keyDown(listbox, { key: 'Enter' })

    await waitFor(() => expect(bridge.request).toHaveBeenCalledWith('profile.switch', { profileId: 'p-b' }))
    expect(trigger).toBeDisabled()
    expect(screen.getByText('正在切换 Profile…')).toBeInTheDocument()
    finishSwitch?.()
  })

  it('restores the active profile when switching is rejected', async () => {
    const bridge = bridgeFixture({
      'profile.list': {
        selectedProfileId: 'p-a', pendingProfileId: null, lastKnownGoodProfileId: 'p-a',
        profiles: [{ id: 'p-a', name: 'A', status: 'active', revision: 1 }, { id: 'p-b', name: 'B', status: 'ready', revision: 1 }],
      },
    })
    vi.mocked(bridge.request).mockImplementation(async (action) => {
      if (action === 'profile.list') return {
        selectedProfileId: 'p-a', pendingProfileId: null, lastKnownGoodProfileId: 'p-a',
        profiles: [{ id: 'p-a', name: 'A', status: 'active', revision: 1 }, { id: 'p-b', name: 'B', status: 'ready', revision: 1 }],
      }
      throw new Error('目标 Profile 启动失败')
    })
    render(<ProfileSelector bridge={bridge} />)

    const trigger = await screen.findByRole('button', { name: 'Profile：A' })
    fireEvent.click(trigger)
    fireEvent.click(screen.getByRole('option', { name: /B/ }))

    expect(await screen.findByText('目标 Profile 启动失败')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Profile：A' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Profile：A' })).toHaveFocus()
  })
})
