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
      if (action === 'harness.connection.list') return [{ profileId: 'his-db', kind: 'database', providerId: 'generic', displayName: 'HIS 只读库', endpoint: 'db.internal', readOnly: true, enabled: true }]
      return { credentialId: 'credential-1', status: 'configured' }
    }) as DesktopBridgeLike['requestV2'],
    dispose: vi.fn(),
  }
}

describe('model and agent center', () => {
  it('loads providers and exposes the bounded management tabs', async () => {
    const bridge = bridgeFixture()
    render(<ModelAgentCenter bridge={bridge} />)

    expect(await screen.findByText('Codex')).toBeVisible()
    expect(screen.getByText('未配置')).toBeVisible()
    expect(screen.getByRole('tab', { name: 'API 模型' })).toBeVisible()
    expect(screen.getByRole('tab', { name: 'Agents' })).toBeVisible()
    expect(screen.queryByRole('tab', { name: 'Harness 任务' })).toBeNull()
    expect(screen.getByRole('tab', { name: 'MCP 连接维护' })).toBeVisible()
    expect(screen.getByRole('tab', { name: '数据库维护' })).toBeVisible()
    expect(screen.getByRole('tab', { name: 'Diagnostics' })).toBeVisible()
    expect(screen.queryByRole('tab', { name: 'Extensions' })).toBeNull()

    fireEvent.click(screen.getByRole('tab', { name: 'Diagnostics' }))
    expect(await screen.findByText('读取文件')).toBeVisible()
  })

  it('keeps database maintenance separate from the Harness task selector', async () => {
    const bridge = bridgeFixture()
    render(<ModelAgentCenter bridge={bridge} />)
    fireEvent.click(await screen.findByRole('tab', { name: '数据库维护' }))
    expect(await screen.findByText('HIS 只读库')).toBeVisible()
    expect(screen.getByText(/数据库独立维护/)).toBeVisible()
  })

  it('maintains concrete Yunxiao and GitLab business connections instead of generic MCP links', async () => {
    const bridge = bridgeFixture()
    render(<ModelAgentCenter bridge={bridge} />)
    fireEvent.click(await screen.findByRole('tab', { name: 'MCP 连接维护' }))
    expect(await screen.findByText('云效需求读取')).toBeVisible()
    expect(screen.getByText('GitLab 代码读取')).toBeVisible()
    expect(screen.queryByText('其他 MCP')).toBeNull()
    expect(screen.queryByLabelText('MCP 地址')).toBeNull()
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
