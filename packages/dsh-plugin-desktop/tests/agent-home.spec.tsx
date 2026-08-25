import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AgentHome } from '../src/client/AgentHome'
import type { DesktopBridgeLike } from '../src/client/desktop-bridge'

function bridgeFixture(responses: Record<string, unknown> = {}): DesktopBridgeLike {
  const requestV2 = vi.fn(
    async (action: string, _context: unknown, payload: Record<string, unknown>) => {
      if (action === 'cli.path.select') {
        if (typeof payload.path === 'string' && payload.path === '/opt/homebrew/bin/codex') return {}
        throw new Error('选择的路径不是可用的 CLI：请确认文件存在、可执行，并且能返回版本号')
      }
      if (action in responses) return responses[action]
      throw new Error(`unexpected action: ${action}`)
    },
  )
  return {
    requestV2: requestV2 as unknown as DesktopBridgeLike['requestV2'],
    request: vi.fn(async () => { throw new Error('v1 not used') }) as unknown as DesktopBridgeLike['request'],
    dispose: vi.fn(),
  }
}

const providers = [
  { providerId: 'codex', displayName: 'Codex', credentialStatus: 'configured' },
  { providerId: 'claude', displayName: 'Claude' },
]

describe('AgentHome', () => {
  it('guides through install and login when Codex CLI is missing', async () => {
    const bridge = bridgeFixture({
      'provider.metadata.list': providers,
      'cli.login.status': { installed: false, jobRunning: false, jobOutput: [] },
      'cli.install.status': { command: ['npm', 'install', '-g', '@openai/codex'], installed: false, jobRunning: false, jobOutput: [] },
    })
    render(<AgentHome bridge={bridge} workspaceId="workspace-1" onOpenWorkbench={() => undefined} />)
    await waitFor(() => expect(screen.getByRole('button', { name: '安装 Codex CLI' })).toBeInTheDocument())
    expect(screen.queryByRole('button', { name: '登录官方账号' })).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: '安装 Codex CLI' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('cli.install.start', undefined, { providerId: 'codex' }))
  })

  it('offers official login when installed but logged out, and blocks the workbench until ready', async () => {
    const bridge = bridgeFixture({
      'provider.metadata.list': providers,
      'cli.login.status': { installed: true, cliPath: '/opt/homebrew/bin/codex', loggedIn: false, jobRunning: false, jobOutput: [] },
      'cli.install.status': { command: ['npm', 'install', '-g', '@openai/codex'], installed: true, jobRunning: false, jobOutput: [] },
    })
    render(<AgentHome bridge={bridge} workspaceId="workspace-1" onOpenWorkbench={() => undefined} />)
    await waitFor(() => expect(screen.getByRole('button', { name: '登录官方账号' })).toBeInTheDocument())
    const entry = screen.getByRole('button', { name: /进入 Codex 工作台/ })
    expect(entry).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '登录官方账号' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('cli.login.start', undefined, { providerId: 'codex' }))
  })

  it('enters the Codex workbench once CLI and login are ready', async () => {
    const bridge = bridgeFixture({
      'provider.metadata.list': providers,
      'cli.login.status': { installed: true, cliPath: '/opt/homebrew/bin/codex', loggedIn: true, mode: '已登录（ChatGPT）', jobRunning: false, jobOutput: [] },
      'cli.install.status': { command: ['npm', 'install', '-g', '@openai/codex'], installed: true, jobRunning: false, jobOutput: [] },
    })
    const onOpenWorkbench = vi.fn()
    render(<AgentHome bridge={bridge} workspaceId="workspace-1" onOpenWorkbench={onOpenWorkbench} />)
    const entry = await screen.findByRole('button', { name: /进入 Codex 工作台/ })
    expect(entry).toBeEnabled()
    fireEvent.click(entry)
    expect(onOpenWorkbench).toHaveBeenCalledWith('codex')
    expect(await screen.findByText(/已登录/)).toBeInTheDocument()
  })

  it('disables entry and explains when no workspace is selected', async () => {
    const bridge = bridgeFixture({
      'provider.metadata.list': providers,
      'cli.login.status': { installed: true, loggedIn: true, jobRunning: false, jobOutput: [] },
      'cli.install.status': { command: [], installed: true, jobRunning: false, jobOutput: [] },
    })
    render(<AgentHome bridge={bridge} onOpenWorkbench={() => undefined} />)
    expect(await screen.findByText(/在左侧打开或创建一个项目/)).toBeInTheDocument()
    const entry = screen.getByRole('button', { name: /进入 Codex 工作台/ })
    expect(entry).toBeDisabled()
  })

  it('saves a manual CLI path through the advanced fallback', async () => {
    const bridge = bridgeFixture({
      'provider.metadata.list': providers,
      'cli.login.status': { installed: false, jobRunning: false, jobOutput: [] },
      'cli.install.status': { command: [], installed: false, jobRunning: false, jobOutput: [] },
    })
    render(<AgentHome bridge={bridge} workspaceId="workspace-1" onOpenWorkbench={() => undefined} />)
    fireEvent.click(await screen.findByText('高级：自动检测不到 CLI？手动指定路径'))
    const input = screen.getByPlaceholderText('/opt/homebrew/bin/codex')
    fireEvent.change(input, { target: { value: '/opt/homebrew/bin/codex' } })
    fireEvent.click(screen.getByRole('button', { name: '保存并检测' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('cli.path.select', undefined, { providerId: 'codex', path: '/opt/homebrew/bin/codex' }))
    expect(await screen.findByText(/已保存/)).toBeInTheDocument()
  })

  it('shows the actionable message when a manual path is invalid', async () => {
    const bridge = bridgeFixture({
      'provider.metadata.list': providers,
      'cli.login.status': { installed: false, jobRunning: false, jobOutput: [] },
      'cli.install.status': { command: [], installed: false, jobRunning: false, jobOutput: [] },
    })
    render(<AgentHome bridge={bridge} workspaceId="workspace-1" onOpenWorkbench={() => undefined} />)
    fireEvent.click(await screen.findByText('高级：自动检测不到 CLI？手动指定路径'))
    const input = screen.getByPlaceholderText('/opt/homebrew/bin/codex')
    fireEvent.change(input, { target: { value: '/not/a/cli' } })
    fireEvent.click(screen.getByRole('button', { name: '保存并检测' }))
    expect(await screen.findByText(/不是可用的 CLI/)).toBeInTheDocument()
  })
})
