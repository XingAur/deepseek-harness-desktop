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

const LIST = [{ id: 'p1', title: '默认提示词', updatedAt: 1, activatedTargets: ['claude' as const] }]
const PRESET = { id: 'p1', title: '默认提示词', content: '# 正文', createdAt: 0, updatedAt: 1 }

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

  it('选择预设进入编辑器,保存走 prompts.save 并刷新', async () => {
    const bridge = bridgeWith({
      'prompts.status': () => STATUS,
      'prompts.list': () => LIST,
      'prompts.get': () => PRESET,
      'prompts.save': () => ({ kind: 'saved', preset: { ...PRESET, content: '# 新正文', updatedAt: 2 }, projected: STATUS }),
    })
    render(<PromptsPanel bridge={bridge} />)
    fireEvent.click(await screen.findByRole('button', { name: /默认提示词/ }))
    const editor = await screen.findByRole('textbox', { name: '预设内容' })
    expect(editor).toHaveValue('# 正文')
    fireEvent.change(editor, { target: { value: '# 新正文' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('prompts.save', undefined, { presetId: 'p1', title: '默认提示词', content: '# 新正文' }))
    expect(await screen.findByText(/预览/)).toBeInTheDocument()
  })

  it('保存返回冲突时弹对话框,选定候选走 prompts.resolve-conflict', async () => {
    const bridge = bridgeWith({
      'prompts.status': () => STATUS,
      'prompts.list': () => LIST,
      'prompts.get': () => ({ ...PRESET, content: 'v1' }),
      'prompts.save': () => ({ kind: 'backfill-conflict', presetId: 'p1', candidates: [
        { target: 'claude', content: 'claude 端内容', updatedAt: 5 },
        { target: 'codex', content: 'codex 端内容', updatedAt: 6 },
      ] }),
      'prompts.resolve-conflict': () => ({ kind: 'saved', preset: { ...PRESET, content: 'claude 端内容', updatedAt: 7 }, projected: STATUS }),
    })
    render(<PromptsPanel bridge={bridge} />)
    fireEvent.click(await screen.findByRole('button', { name: /默认提示词/ }))
    fireEvent.click(await screen.findByRole('button', { name: '保存' }))
    expect(await screen.findByRole('dialog', { name: '检测到外部修改' })).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('radio', { name: /Claude/ }))
    fireEvent.click(screen.getByRole('button', { name: '以此为准并保存' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('prompts.resolve-conflict', undefined, { presetId: 'p1', title: '默认提示词', content: 'claude 端内容' }))
  })

  it('勾选激活目标走 prompts.activate,停用走 prompts.deactivate', async () => {
    const bridge = bridgeWith({
      'prompts.status': () => STATUS.map((entry) => ({ ...entry, activePresetId: entry.target === 'claude' ? 'p1' : null })),
      'prompts.list': () => LIST,
      'prompts.get': () => PRESET,
      'prompts.activate': () => ({ kind: 'ok', status: STATUS[2] }),
      'prompts.deactivate': () => STATUS[0],
    })
    render(<PromptsPanel bridge={bridge} />)
    fireEvent.click(await screen.findByRole('button', { name: /默认提示词/ }))
    const group = await screen.findByRole('group', { name: '激活目标' })
    fireEvent.click(group.querySelector('input[value="dsh"]') as HTMLElement)
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('prompts.activate', undefined, { presetId: 'p1', target: 'dsh' }))
    fireEvent.click(screen.getByRole('button', { name: '停用已激活目标' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('prompts.deactivate', undefined, { target: 'claude' }))
  })
})
