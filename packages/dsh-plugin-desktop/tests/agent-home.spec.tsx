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

const notInstalled = {
  'cli.login.status': { installed: false, jobRunning: false, jobOutput: [] },
  'cli.install.status': { command: ['npm', 'install', '-g', '@openai/codex'], installed: false, jobRunning: false, jobOutput: [] },
}

describe('AgentHome', () => {
  it('只呈现 Codex：没有 Claude 或其他 Provider 卡片', async () => {
    render(<AgentHome bridge={bridgeFixture(notInstalled)} workspaceId="workspace-1" />)
    await screen.findByRole('button', { name: '安装 Codex CLI' })
    expect(screen.queryByText(/Claude/)).toBeNull()
    expect(screen.queryByText(/即将支持/)).toBeNull()
  })

  it('未安装时引导安装，登录按钮不出现', async () => {
    const bridge = bridgeFixture(notInstalled)
    render(<AgentHome bridge={bridge} workspaceId="workspace-1" />)
    fireEvent.click(await screen.findByRole('button', { name: '安装 Codex CLI' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('cli.install.start', undefined, { providerId: 'codex' }))
  })

  it('已安装未登录时提供登录入口，工作台按钮保持禁用', async () => {
    const bridge = bridgeFixture({
      'cli.login.status': { installed: true, cliPath: '/opt/homebrew/bin/codex', loggedIn: false, jobRunning: false, jobOutput: [] },
      'cli.install.status': { command: [], installed: true, jobRunning: false, jobOutput: [] },
    })
    render(<AgentHome bridge={bridge} workspaceId="workspace-1" />)
    fireEvent.click(await screen.findByRole('button', { name: '登录官方账号' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('cli.login.start', undefined, { providerId: 'codex' }))
  })

  it('登录探测中显示确认文案且不出现登录按钮', async () => {
    render(<AgentHome bridge={bridgeFixture({
      'cli.login.status': { installed: true, cliPath: '/x/codex', loggedIn: undefined, jobRunning: false, jobOutput: [] },
      'cli.install.status': { command: [], installed: true, jobRunning: false, jobOutput: [] },
    })} workspaceId="workspace-1" />)
    expect(await screen.findByText(/正在确认登录状态/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '登录官方账号' })).toBeNull()
  })

  it('全部就绪时提示到主聊天模型选择器使用', async () => {
    render(<AgentHome bridge={bridgeFixture({
      'cli.login.status': { installed: true, cliPath: '/x/codex', loggedIn: true, mode: '已登录 · ChatGPT 官方账号', jobRunning: false, jobOutput: [] },
      'cli.install.status': { command: [], installed: true, jobRunning: false, jobOutput: [] },
    })} workspaceId="workspace-1" />)
    expect(await screen.findByText(/模型选择器里选择/)).toBeInTheDocument()
    expect(await screen.findByText(/ChatGPT 官方账号/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /工作台/ })).toBeNull()
  })

  it('登录探测失败时展示人话原因', async () => {
    render(<AgentHome bridge={bridgeFixture({
      'cli.login.status': { installed: true, cliPath: '/x/codex', loggedIn: false, detail: '确认登录状态超时了。CLI 已安装，可以点「重新检测」再试，或直接进入工作台使用。', jobRunning: false, jobOutput: [] },
      'cli.install.status': { command: [], installed: true, jobRunning: false, jobOutput: [] },
    })} workspaceId="workspace-1" />)
    expect(await screen.findByText(/确认登录状态超时/)).toBeInTheDocument()
  })

  it('无工作区时说明下一步并禁用工作台按钮', async () => {
    render(<AgentHome bridge={bridgeFixture({
      'cli.login.status': { installed: true, loggedIn: true, mode: '已登录', jobRunning: false, jobOutput: [] },
      'cli.install.status': { command: [], installed: true, jobRunning: false, jobOutput: [] },
    })} />)
    expect(await screen.findByText(/在左侧打开或创建一个项目/)).toBeInTheDocument()
  })

  it('高级入口可保存手动 CLI 路径并反馈结果', async () => {
    const bridge = bridgeFixture(notInstalled)
    render(<AgentHome bridge={bridge} workspaceId="workspace-1" />)
    fireEvent.click(await screen.findByText('高级：自动检测不到 CLI？手动指定路径'))
    fireEvent.change(screen.getByPlaceholderText('/opt/homebrew/bin/codex'), { target: { value: '/opt/homebrew/bin/codex' } })
    fireEvent.click(screen.getByRole('button', { name: '保存并检测' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('cli.path.select', undefined, { providerId: 'codex', path: '/opt/homebrew/bin/codex' }))
    expect(await screen.findByText(/已保存并检测通过/)).toBeInTheDocument()
  })
})
