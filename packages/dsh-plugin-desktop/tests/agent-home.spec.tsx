import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AgentHome } from '../src/client/AgentHome'
import type { DesktopBridgeLike } from '../src/client/desktop-bridge'

function bridgeFixture(responses: Record<string, unknown> = {}): DesktopBridgeLike {
  const requestV2 = vi.fn(
    async (action: string, _context: unknown, _payload: Record<string, unknown>) => {
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
    await waitFor(() => expect(screen.getByText('安装 Codex CLI')).toBeInTheDocument())
    expect(screen.queryByText('登录官方账号')).toBeNull()
    fireEvent.click(screen.getByText('安装 Codex CLI'))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('cli.install.start', undefined, { providerId: 'codex' }))
  })

  it('offers official login when installed but logged out, and blocks the workbench until ready', async () => {
    const bridge = bridgeFixture({
      'provider.metadata.list': providers,
      'cli.login.status': { installed: true, cliPath: '/opt/homebrew/bin/codex', loggedIn: false, jobRunning: false, jobOutput: [] },
      'cli.install.status': { command: ['npm', 'install', '-g', '@openai/codex'], installed: true, jobRunning: false, jobOutput: [] },
    })
    render(<AgentHome bridge={bridge} workspaceId="workspace-1" onOpenWorkbench={() => undefined} />)
    await waitFor(() => expect(screen.getByText('登录官方账号')).toBeInTheDocument())
    const entry = screen.getByRole('button', { name: /进入 Codex 工作台/ })
    expect(entry).toBeDisabled()
    fireEvent.click(screen.getByText('登录官方账号'))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('cli.login.start', undefined, { providerId: 'codex' }))
  })

  it('enters the Codex workbench once CLI and login are ready', async () => {
    const bridge = bridgeFixture({
      'provider.metadata.list': providers,
      'cli.login.status': { installed: true, cliPath: '/opt/homebrew/bin/codex', loggedIn: true, mode: 'ChatGPT 官方账号', jobRunning: false, jobOutput: [] },
      'cli.install.status': { command: ['npm', 'install', '-g', '@openai/codex'], installed: true, jobRunning: false, jobOutput: [] },
    })
    const onOpenWorkbench = vi.fn()
    render(<AgentHome bridge={bridge} workspaceId="workspace-1" onOpenWorkbench={onOpenWorkbench} />)
    const entry = await screen.findByRole('button', { name: /进入 Codex 工作台/ })
    expect(entry).toBeEnabled()
    fireEvent.click(entry)
    expect(onOpenWorkbench).toHaveBeenCalledWith('codex')
    expect(await screen.findByText('ChatGPT 官方账号')).toBeInTheDocument()
  })

  it('disables entry when no workspace is selected', async () => {
    const bridge = bridgeFixture({
      'provider.metadata.list': providers,
      'cli.login.status': { installed: true, loggedIn: true, jobRunning: false, jobOutput: [] },
      'cli.install.status': { command: [], installed: true, jobRunning: false, jobOutput: [] },
    })
    render(<AgentHome bridge={bridge} onOpenWorkbench={() => undefined} />)
    expect(await screen.findByText(/还没有可用工作区/)).toBeInTheDocument()
  })
})
