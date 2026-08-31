import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PromptsPanel } from '../src/client/extension-center/PromptsPanel'
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

const STATUS = [
  { target: 'claude', installed: true, liveFileExists: true, activePresetId: 'p1', liveContentSha256: 'aa', matchesActivePreset: true, oversized: false },
  { target: 'codex', installed: false, liveFileExists: false, activePresetId: null, liveContentSha256: null, matchesActivePreset: false, oversized: false },
  { target: 'dsh', installed: true, liveFileExists: false, activePresetId: null, liveContentSha256: null, matchesActivePreset: false, oversized: false },
]

describe('PromptsPanel', () => {
  it('加载目标状态与预设列表并渲染状态条', async () => {
    const bridge = bridgeWith({
      'prompts.status': () => STATUS,
      'prompts.list': () => [{ id: 'p1', title: '默认提示词', updatedAt: 1, activatedTargets: ['claude'] }],
    })
    render(<PromptsPanel bridge={bridge} />)
    expect(await screen.findByText('Claude')).toBeInTheDocument()
    expect(await screen.findByText('默认提示词')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Codex' })).toBeDisabled()
    expect(screen.queryByText(/外部修改/)).not.toBeInTheDocument()
  })

  it('live 哈希与激活预设不一致时亮出外部修改徽标', async () => {
    const bridge = bridgeWith({
      'prompts.status': () => STATUS.map((entry) => entry.target === 'claude' ? { ...entry, matchesActivePreset: false } : entry),
      'prompts.list': () => [],
    })
    render(<PromptsPanel bridge={bridge} />)
    expect(await screen.findByText(/外部修改/)).toBeInTheDocument()
  })

  it('预设池为空且存在非空 live 文件时弹首启导入对话框', async () => {
    const bridge = bridgeWith({
      'prompts.status': () => STATUS.map((entry) => entry.target === 'claude' ? { ...entry, liveFileExists: true, activePresetId: null } : entry),
      'prompts.list': () => [],
    })
    render(<PromptsPanel bridge={bridge} />)
    expect(await screen.findByRole('dialog', { name: '导入现有提示词' })).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('checkbox', { name: 'Claude' }))
    fireEvent.click(screen.getByRole('button', { name: '导入' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('prompts.import', undefined, { targets: ['claude'] }))
  })
})
