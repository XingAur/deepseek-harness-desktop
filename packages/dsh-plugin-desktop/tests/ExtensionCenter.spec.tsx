import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ExtensionCenter } from '../src/client/extensions/ExtensionCenter'
import type { DesktopBridgeLike } from '../src/client/desktop-bridge'

describe('ExtensionCenter', () => {
  it('shows an explicit review before enabling an extension and uses the scoped bridge action', async () => {
    const extension = { extensionId: 'demo.plugin', extensionKind: 'plugin', displayName: 'Demo', sourceKind: 'builtin', status: 'disabled', updatedAt: 'now' }
    const bridge: DesktopBridgeLike = {
      request: vi.fn(),
      requestV2: vi.fn().mockResolvedValue({ ...extension, status: 'enabled' }) as DesktopBridgeLike['requestV2'],
      dispose: vi.fn(),
    }
    const onChange = vi.fn()
    render(<ExtensionCenter bridge={bridge} extensions={[extension]} onChange={onChange} />)
    fireEvent.click(screen.getByRole('button', { name: '查看审核' }))
    expect(screen.getByRole('dialog', { name: '扩展审核' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '关闭' }))
    fireEvent.click(screen.getByRole('button', { name: '启用' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('extension.enable', undefined, { extensionId: 'demo.plugin' }))
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ status: 'enabled' }))
  })
})
