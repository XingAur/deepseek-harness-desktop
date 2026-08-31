import { render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PluginMarket } from '../src/client/extensions/PluginMarket'
import type { DesktopBridgeLike } from '../src/client/desktop-bridge'

describe('PluginMarket', () => {
  it('renders as the market tab inside the official plugin settings section', async () => {
    const requestV2 = vi.fn(async (action: string) => {
      if (action === 'plugin.catalog.list') {
        return { total: 0, offset: 0, categories: [], entries: [] }
      }
      if (action === 'plugin.install.status') {
        return { jobRunning: false, jobOutput: [] }
      }
      throw new Error(`unexpected action: ${action}`)
    })
    const bridge = {
      requestV2,
      request: vi.fn(),
      dispose: vi.fn(),
    } as unknown as DesktopBridgeLike

    render(<PluginMarket bridge={bridge} embedded />)

    expect(screen.getByRole('region', { name: '社区插件市场' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: '社区插件市场', level: 3 })).toBeInTheDocument()
    expect(screen.queryByText(/设置 → 插件/)).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '插件市场', level: 2 })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '关闭' })).not.toBeInTheDocument()
    await waitFor(() => expect(requestV2).toHaveBeenCalledWith(
      'plugin.catalog.list',
      undefined,
      expect.objectContaining({ limit: 30 }),
    ))
    expect(requestV2).not.toHaveBeenCalledWith('extension.inventory')
  })
})
