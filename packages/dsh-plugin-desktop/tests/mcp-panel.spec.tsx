import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { McpPanel, draftFromServer, emptyDraft } from '../src/client/extension-center/McpPanel'
import {
  argsToText, envToText, parseArgsText, parseEnvText, targetSummary,
  type McpServerDef,
} from '../src/client/extension-center/mcp-api'
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
  { target: 'claude', installed: true },
  { target: 'codex', installed: false },
]

const BOTH_INSTALLED = [
  { target: 'claude', installed: true },
  { target: 'codex', installed: true },
] as const

const SERVER: McpServerDef = {
  id: 'srv-1',
  name: 'fetch',
  command: 'npx',
  args: ['-y', 'server-fetch'],
  env: { NO_PROXY: '127.0.0.1' },
  targets: ['claude'],
}

describe('McpPanel', () => {
  it('加载目标状态与服务器列表并渲染徽标与摘要', async () => {
    const bridge = bridgeWith({
      'mcp.list': () => [SERVER],
      'mcp.status': () => STATUS,
    })
    render(<McpPanel bridge={bridge} />)
    expect(await screen.findByText('fetch')).toBeInTheDocument()
    expect(screen.getByText('npx -y server-fetch')).toBeInTheDocument()
    const statusRow = screen.getByRole('group', { name: '目标状态' })
    expect(statusRow).toHaveTextContent('Claude已安装')
    expect(statusRow).toHaveTextContent('Codex未安装')
    expect(screen.getByText('Claude', { selector: '.dshMcpTargetBadge' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '从 Codex 导入' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '从 Claude 导入' })).toBeEnabled()
  })

  it('添加服务器走 mcp.upsert 且不带 id,完成后刷新列表', async () => {
    const bridge = bridgeWith({
      'mcp.list': vi.fn().mockResolvedValueOnce([]).mockResolvedValue([SERVER]),
      'mcp.status': () => STATUS,
      'mcp.upsert': () => SERVER,
    })
    render(<McpPanel bridge={bridge} />)
    fireEvent.click(await screen.findByRole('button', { name: '添加服务器' }))
    const dialog = await screen.findByRole('dialog', { name: '添加服务器' })
    fireEvent.change(screen.getByRole('textbox', { name: '服务器名称' }), { target: { value: 'fetch' } })
    fireEvent.change(screen.getByRole('textbox', { name: '服务器命令' }), { target: { value: 'npx' } })
    fireEvent.change(screen.getByRole('textbox', { name: '服务器参数' }), { target: { value: '-y server-fetch' } })
    fireEvent.change(screen.getByRole('textbox', { name: '服务器环境变量' }), { target: { value: 'NO_PROXY=127.0.0.1\nBAD LINE' } })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Claude' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Codex' }))
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('mcp.upsert', undefined, {
      name: 'fetch',
      command: 'npx',
      args: ['-y', 'server-fetch'],
      env: { NO_PROXY: '127.0.0.1' },
      targets: ['claude', 'codex'],
    }))
    await waitFor(() => expect(dialog).not.toBeInTheDocument())
    // 保存成功后 refreshAll 再拉取一次列表
    await waitFor(() => {
      expect((bridge.requestV2 as ReturnType<typeof vi.fn>).mock.calls.filter(([action]) => action === 'mcp.list')).toHaveLength(2)
    })
  })

  it('名称/命令/目标未填齐时保存按钮禁用', async () => {
    const bridge = bridgeWith({
      'mcp.list': () => [],
      'mcp.status': () => STATUS,
    })
    render(<McpPanel bridge={bridge} />)
    fireEvent.click(await screen.findByRole('button', { name: '添加服务器' }))
    const save = await screen.findByRole('button', { name: '保存' })
    expect(save).toBeDisabled()
    fireEvent.change(screen.getByRole('textbox', { name: '服务器名称' }), { target: { value: 'fetch' } })
    fireEvent.change(screen.getByRole('textbox', { name: '服务器命令' }), { target: { value: 'npx' } })
    expect(save).toBeDisabled()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Claude' }))
    expect(save).toBeEnabled()
  })

  it('同步到目标走 mcp.sync 且未安装目标时禁用', async () => {
    const bridge = bridgeWith({
      'mcp.list': () => [SERVER],
      'mcp.status': () => STATUS,
    })
    render(<McpPanel bridge={bridge} />)
    const sync = await screen.findByRole('button', { name: '同步到目标' })
    expect(sync).toBeEnabled()
    fireEvent.click(sync)
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('mcp.sync', undefined, { target: 'claude' }))

    const codexOnly = bridgeWith({
      'mcp.list': () => [{ ...SERVER, id: 'srv-2', targets: ['codex'] }],
      'mcp.status': () => STATUS,
    })
    const second = render(<McpPanel bridge={codexOnly} />)
    const secondSync = await within(second.container).findByRole('button', { name: '同步到目标' })
    expect(secondSync).toBeDisabled()
  })

  it('同步会协调所有已安装目标，以清理编辑后取消勾选的投影', async () => {
    const bothTargets = { ...SERVER, targets: ['claude', 'codex'] as const }
    const edited = { ...SERVER, targets: ['codex'] as const }
    const bridge = bridgeWith({
      'mcp.list': vi.fn().mockResolvedValueOnce([bothTargets]).mockResolvedValue([edited]),
      'mcp.status': () => BOTH_INSTALLED,
      'mcp.upsert': () => edited,
      'mcp.sync': () => undefined,
    })
    render(<McpPanel bridge={bridge} />)
    fireEvent.click(await screen.findByRole('button', { name: '编辑' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Claude' }))
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await screen.findByText('fetch')
    fireEvent.click(screen.getByRole('button', { name: '同步到目标' }))
    await waitFor(() => {
      expect(bridge.requestV2).toHaveBeenCalledWith('mcp.sync', undefined, { target: 'claude' })
      expect(bridge.requestV2).toHaveBeenCalledWith('mcp.sync', undefined, { target: 'codex' })
    })
  })

  it('删除走 mcp.delete,导入走 mcp.import', async () => {
    const bridge = bridgeWith({
      'mcp.list': () => [SERVER],
      'mcp.status': () => STATUS,
      'mcp.import': () => [SERVER],
    })
    render(<McpPanel bridge={bridge} />)
    fireEvent.click(await screen.findByRole('button', { name: '从 Claude 导入' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('mcp.import', undefined, { target: 'claude' }))
    fireEvent.click(screen.getByRole('button', { name: '删除' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('mcp.delete', undefined, { id: 'srv-1' }))
  })

  it('编辑服务器回填表单且 upsert 携带 id', async () => {
    const bridge = bridgeWith({
      'mcp.list': () => [SERVER],
      'mcp.status': () => STATUS,
      'mcp.upsert': () => SERVER,
    })
    render(<McpPanel bridge={bridge} />)
    fireEvent.click(await screen.findByRole('button', { name: '编辑' }))
    await screen.findByRole('dialog', { name: '编辑服务器' })
    expect(screen.getByRole('textbox', { name: '服务器名称' })).toHaveValue('fetch')
    expect(screen.getByRole('textbox', { name: '服务器参数' })).toHaveValue('-y server-fetch')
    expect(screen.getByRole('checkbox', { name: 'Claude' })).toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('mcp.upsert', undefined, {
      id: 'srv-1',
      name: 'fetch',
      command: 'npx',
      args: ['-y', 'server-fetch'],
      env: { NO_PROXY: '127.0.0.1' },
      targets: ['claude'],
    }))
  })
})

describe('mcp-api helpers', () => {
  it('参数按空白分词', () => {
    expect(parseArgsText('')).toEqual([])
    expect(parseArgsText('  -y   server-fetch ')).toEqual(['-y', 'server-fetch'])
    expect(argsToText(['-y', 'server-fetch'])).toBe('-y server-fetch')
  })

  it('环境变量逐行 KEY=VALUE,坏行跳过', () => {
    expect(parseEnvText('A=1\nBAD\n=2\nB C=3\nD=4')).toEqual({ A: '1', D: '4' })
    expect(envToText({ A: '1' })).toBe('A=1')
  })

  it('目标摘要', () => {
    expect(targetSummary(['claude', 'codex'])).toBe('Claude / Codex')
    expect(targetSummary([])).toBe('未选择目标')
  })

  it('空草稿与草稿回填', () => {
    expect(emptyDraft()).toMatchObject({ id: null, name: '', targets: [] })
    expect(draftFromServer(SERVER)).toMatchObject({ id: 'srv-1', name: 'fetch', argsText: '-y server-fetch', envText: 'NO_PROXY=127.0.0.1' })
  })
})
