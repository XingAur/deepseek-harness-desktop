import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi, type Mock } from 'vitest'
import { PluginsPanel } from '../src/client/extension-center/PluginsPanel'
import type { DesktopBridgeLike } from '../src/client/desktop-bridge'

function bridgeWith(handlers: Record<string, (payload?: Record<string, unknown>) => unknown>): DesktopBridgeLike {
  return {
    request: vi.fn().mockRejectedValue(new Error('v1 不可用')),
    requestV2: vi.fn().mockImplementation((action: string, _context?: unknown, payload?: Record<string, unknown>) => {
      const handler = handlers[action]
      if (handler === undefined) return Promise.reject(new Error(`未模拟的动作 ${action}`))
      return Promise.resolve(handler(payload))
    }),
    dispose: () => undefined,
  }
}

const PLUGIN = { extensionId: 'demo.plugin', extensionKind: 'plugin', displayName: '演示插件', sourceKind: 'community', status: 'disabled', updatedAt: '2026-08-01' }
const MCP = { extensionId: 'demo.mcp', extensionKind: 'mcp', displayName: '演示 MCP', sourceKind: 'community', status: 'enabled', updatedAt: '2026-08-02' }

const CATALOG_PAGE = {
  total: 1,
  offset: 0,
  categories: [{ id: 'tool', count: 1 }],
  entries: [{ id: 'acme/tool', displayName: 'Acme Tool', repo: 'https://github.com/acme/tool', category: 'tool', descriptionZh: '第一个工具插件', descriptionEn: 'first' }],
}

function marketHandlers(): Record<string, (payload?: Record<string, unknown>) => unknown> {
  return {
    'plugin.catalog.list': () => CATALOG_PAGE,
    'plugin.install.status': () => ({ jobRunning: false, jobOutput: [] }),
  }
}

function inventoryCalls(request: Mock): number {
  return request.mock.calls.filter(([action]) => action === 'extension.inventory').length
}

describe('PluginsPanel', () => {
  it('拉取 inventory 后渲染已装扩展列表与插件市场', async () => {
    const bridge = bridgeWith({ 'extension.inventory': () => [PLUGIN, MCP], ...marketHandlers() })
    render(<PluginsPanel bridge={bridge} />)
    expect(await screen.findByText('演示插件')).toBeInTheDocument()
    expect(screen.getByText('已停用')).toBeInTheDocument()
    expect(screen.getByText('演示 MCP')).toBeInTheDocument()
    expect(screen.getByText('已启用')).toBeInTheDocument()
    // 插件市场仍在(ExtensionCenter 底部嵌入的 PluginMarket)
    expect(screen.getByRole('heading', { name: '插件市场' })).toBeInTheDocument()
    expect(await screen.findByText('Acme Tool')).toBeInTheDocument()
    const request = bridge.requestV2 as unknown as Mock
    expect(inventoryCalls(request)).toBe(1)
  })

  it('启用扩展时以正确载荷调用 extension.enable,之后重拉 inventory', async () => {
    let enabled = false
    const bridge = bridgeWith({
      'extension.inventory': () => [{ ...PLUGIN, status: enabled ? 'enabled' : 'disabled' }],
      'extension.enable': () => { enabled = true; return { ...PLUGIN, status: 'enabled' } },
      ...marketHandlers(),
    })
    render(<PluginsPanel bridge={bridge} />)
    fireEvent.click(await screen.findByRole('button', { name: '启用' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('extension.enable', undefined, { extensionId: 'demo.plugin' }))
    const request = bridge.requestV2 as unknown as Mock
    await waitFor(() => expect(inventoryCalls(request)).toBe(2))
    // 重拉后的宿主状态回流:按钮从「启用」变为「停用」
    expect(await screen.findByRole('button', { name: '停用' })).toBeInTheDocument()
  })

  it('点击「刷新」重新拉取 inventory', async () => {
    const bridge = bridgeWith({ 'extension.inventory': () => [PLUGIN], ...marketHandlers() })
    render(<PluginsPanel bridge={bridge} />)
    expect(await screen.findByText('演示插件')).toBeInTheDocument()
    // 面板头部的刷新按钮在 DOM 中先于市场自身的刷新按钮
    fireEvent.click(screen.getAllByRole('button', { name: '刷新' })[0])
    const request = bridge.requestV2 as unknown as Mock
    await waitFor(() => expect(inventoryCalls(request)).toBe(2))
  })

  it('inventory 拉取失败时显示错误条不崩溃,插件市场仍可用', async () => {
    const bridge = bridgeWith({ ...marketHandlers() })
    render(<PluginsPanel bridge={bridge} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('未模拟的动作 extension.inventory')
    expect(screen.getByRole('heading', { name: '插件市场' })).toBeInTheDocument()
    expect(screen.queryByText('演示插件')).not.toBeInTheDocument()
  })

  it('inventory 响应形状异常时按错误处理而非崩溃', async () => {
    const bridge = bridgeWith({ 'extension.inventory': () => undefined, ...marketHandlers() })
    render(<PluginsPanel bridge={bridge} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('扩展清单响应异常')
  })
})
