import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ModelAgentCenter } from '../src/client/model-agent/ModelAgentCenter'
import type { DesktopBridgeLike } from '../src/client/desktop-bridge'

function bridgeFixture(): DesktopBridgeLike {
  return {
    request: vi.fn(),
    requestV2: vi.fn(async (action: string) => {
      if (action === 'provider.metadata.list') return [{
        providerId: 'codex', displayName: 'Codex', cliCommand: 'codex',
        adapterProtocol: 'dsh-agent-adapter/v1', credentialSupported: true,
        developerOnly: false, credentialStatus: 'not-configured',
      }]
      if (action === 'capability.inventory') return [{ id: 'file-read', displayName: '读取文件', mutating: false, approvalRequired: false }]
      if (action === 'extension.inventory') return []
      if (action === 'cli.path.status') return { provider: 'codex', selected: null, candidates: [], diagnostics: [] }
      return { credentialId: 'credential-1', status: 'configured' }
    }) as DesktopBridgeLike['requestV2'],
    dispose: vi.fn(),
  }
}

describe('model and agent center', () => {
  it('loads providers and exposes the three bounded management tabs', async () => {
    const bridge = bridgeFixture()
    render(<ModelAgentCenter bridge={bridge} />)

    expect(await screen.findByText('Codex')).toBeVisible()
    expect(screen.getByText('未配置')).toBeVisible()
    expect(screen.getByRole('tab', { name: 'API 模型' })).toBeVisible()
    expect(screen.getByRole('tab', { name: 'Agents' })).toBeVisible()
    expect(screen.getByRole('tab', { name: 'Diagnostics' })).toBeVisible()
    expect(screen.queryByRole('tab', { name: 'Extensions' })).toBeNull()

    fireEvent.click(screen.getByRole('tab', { name: 'Diagnostics' }))
    expect(await screen.findByText('读取文件')).toBeVisible()
  })

  it('Agents 页签渲染 AgentHome(安装/登录入口随其可用)', async () => {
    const bridge = bridgeFixture()
    render(<ModelAgentCenter bridge={bridge} />)

    await screen.findByText('Codex')
    fireEvent.click(screen.getByRole('tab', { name: 'Agents' }))
    expect(await screen.findByRole('button', { name: '安装 Codex CLI' })).toBeVisible()
    expect(screen.getByRole('button', { name: '重新检测' })).toBeVisible()
    expect(screen.getByText('Codex 在你的项目里真实执行任务；写文件、跑命令等操作都会先请求你的批准。')).toBeVisible()
  })

  it('opens the credential flow without rendering the secret', async () => {
    const bridge = bridgeFixture()
    render(<ModelAgentCenter bridge={bridge} />)
    await screen.findByText('Codex')
    fireEvent.click(screen.getByRole('button', { name: '配置 Codex API Key（可选）' }))
    expect(screen.getByRole('dialog')).toBeVisible()
    fireEvent.change(screen.getByLabelText('API Key'), { target: { value: 'sk-secret-value' } })
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(screen.queryByText('sk-secret-value')).not.toBeInTheDocument()
  })
})
