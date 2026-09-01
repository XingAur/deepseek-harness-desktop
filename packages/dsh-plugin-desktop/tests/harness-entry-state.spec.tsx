import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  isHarnessEntryEnabled,
  setHarnessEntryEnabled,
  subscribeHarnessEntry,
} from '../src/client/harness/harness-entry-state'
import { HarnessChatSurface } from '../src/client/harness/HarnessChatSurface'
import { ModelAgentCenter } from '../src/client/model-agent/ModelAgentCenter'
import type { DesktopBridgeLike } from '../src/client/desktop-bridge'

function bridgeFixture(): DesktopBridgeLike {
  return {
    request: vi.fn(),
    requestV2: vi.fn(async (action: string) => {
      if (action === 'harness.connection.list') return []
      return undefined
    }) as DesktopBridgeLike['requestV2'],
    dispose: vi.fn(),
  }
}

function centerBridgeFixture(): DesktopBridgeLike {
  return {
    request: vi.fn(),
    requestV2: vi.fn(async (action: string) => {
      if (action === 'provider.metadata.list') return []
      if (action === 'capability.inventory') return []
      if (action === 'extension.inventory') return []
      if (action === 'harness.status') return { state: 'idle' }
      if (action === 'harness.connection.list') return []
      return { jobRunning: false, jobOutput: [] }
    }) as DesktopBridgeLike['requestV2'],
    dispose: vi.fn(),
  }
}

function HarnessConversation() {
  return (
    <HarnessChatSurface
      bridge={bridgeFixture()}
      renderConversation={() => <div data-testid="official-conversation" />}
    />
  )
}

describe('harness entry state', () => {
  afterEach(() => {
    setHarnessEntryEnabled(false)
    window.localStorage.removeItem('dsh-harness-task-enabled')
  })

  it('defaults to disabled without a stored flag', () => {
    expect(isHarnessEntryEnabled()).toBe(false)
    expect(window.localStorage.getItem('dsh-harness-task-enabled')).toBeNull()
  })

  it('persists the switch in localStorage and notifies subscribers', () => {
    const listener = vi.fn()
    const unsubscribe = subscribeHarnessEntry(listener)

    setHarnessEntryEnabled(true)
    expect(isHarnessEntryEnabled()).toBe(true)
    expect(window.localStorage.getItem('dsh-harness-task-enabled')).toBe('true')
    expect(listener).toHaveBeenCalledTimes(1)

    setHarnessEntryEnabled(false)
    expect(isHarnessEntryEnabled()).toBe(false)
    expect(window.localStorage.getItem('dsh-harness-task-enabled')).toBeNull()
    expect(listener).toHaveBeenCalledTimes(2)

    unsubscribe()
  })

  it('ignores redundant writes without notifying subscribers', () => {
    const listener = vi.fn()
    const unsubscribe = subscribeHarnessEntry(listener)

    setHarnessEntryEnabled(false)
    expect(listener).not.toHaveBeenCalled()

    unsubscribe()
  })

  it('restores the switch from localStorage on module load', async () => {
    window.localStorage.setItem('dsh-harness-task-enabled', 'true')
    vi.resetModules()
    try {
      const restored = await import('../src/client/harness/harness-entry-state')
      expect(restored.isHarnessEntryEnabled()).toBe(true)
    } finally {
      window.localStorage.removeItem('dsh-harness-task-enabled')
      vi.resetModules()
    }
  })

  it('hides the conversation toolbar entry while the switch is off', () => {
    render(<HarnessConversation />)

    expect(screen.getByTestId('official-conversation')).toBeInTheDocument()
    expect(screen.getByText('当前对话')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '开始 Harness 任务' })).toBeNull()
    expect(screen.queryByRole('button', { name: '返回普通聊天' })).toBeNull()
    expect(screen.queryByText('普通聊天和 Harness 任务都从这里开始')).toBeNull()
  })

  it('shows the toolbar entry only while the switch is on', () => {
    render(<HarnessConversation />)
    expect(screen.queryByRole('button', { name: '开始 Harness 任务' })).toBeNull()

    act(() => setHarnessEntryEnabled(true))
    expect(screen.getByRole('button', { name: '开始 Harness 任务' })).toBeInTheDocument()
    expect(screen.getByText('普通聊天和 Harness 任务都从这里开始')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '开始 Harness 任务' }))
    expect(screen.getByRole('region', { name: 'Harness 任务' })).toBeInTheDocument()

    act(() => setHarnessEntryEnabled(false))
    expect(screen.queryByRole('button', { name: '返回普通聊天' })).toBeNull()
    expect(screen.queryByRole('region', { name: 'Harness 任务' })).toBeNull()
    expect(screen.getByTestId('official-conversation')).toBeInTheDocument()
  })

  it('model and agent center hosts the persistent entry switch', () => {
    render(<ModelAgentCenter bridge={centerBridgeFixture()} />)

    const toggle = screen.getByRole('switch', { name: '启用 Harness 任务入口' })
    expect(toggle).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByText('在工作台对话页顶部显示 Harness 任务入口')).toBeInTheDocument()

    fireEvent.click(toggle)
    expect(screen.getByRole('switch', { name: '启用 Harness 任务入口' })).toHaveAttribute('aria-checked', 'true')
    expect(isHarnessEntryEnabled()).toBe(true)
    expect(window.localStorage.getItem('dsh-harness-task-enabled')).toBe('true')
  })
})
