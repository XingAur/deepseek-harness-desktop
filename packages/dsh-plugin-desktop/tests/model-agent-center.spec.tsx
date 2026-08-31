import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ModelAgentCenter } from '../src/client/model-agent/ModelAgentCenter'
import type { DesktopBridgeLike } from '../src/client/desktop-bridge'

function bridgeFixture(): DesktopBridgeLike {
  return {
    request: vi.fn(),
    requestV2: vi.fn(async (action: string) => {
      if (action === 'provider.metadata.list') return [{ providerId: 'codex', displayName: 'Codex', cliCommand: 'codex', adapterProtocol: 'dsh-agent-adapter/v1', credentialSupported: false, developerOnly: false }]
      if (action === 'capability.inventory') return [{ id: 'file-read', displayName: '读取文件', mutating: false, approvalRequired: false }]
      if (action === 'extension.inventory') return []
      if (action === 'harness.status') return { state: 'idle' }
      if (action === 'harness.connection.list') return [{ profileId: 'his-db', kind: 'database', providerId: 'generic', displayName: 'HIS 只读库', endpoint: 'db.internal', readOnly: true, enabled: true }]
      return { jobRunning: false, jobOutput: [] }
    }) as DesktopBridgeLike['requestV2'],
    dispose: vi.fn(),
  }
}

describe('model and agent center', () => {
  it('loads providers and exposes the unified capability tabs', async () => {
    const bridge = bridgeFixture()
    render(<ModelAgentCenter bridge={bridge} />)
    expect(await screen.findByText('Codex')).toBeVisible()
    expect(screen.getByRole('tab', { name: '执行器' })).toBeVisible()
    expect(screen.getByRole('tab', { name: '技能' })).toBeVisible()
    expect(screen.getByRole('tab', { name: 'MCP 与连接' })).toBeVisible()
    expect(screen.getByRole('tab', { name: '诊断' })).toBeVisible()
  })

  it('执行器页签渲染 AgentHome 安装入口', async () => {
    const bridge = bridgeFixture()
    render(<ModelAgentCenter bridge={bridge} />)
    expect(await screen.findByRole('button', { name: '安装 Codex CLI' })).toBeVisible()
    expect(screen.getByRole('button', { name: '重新检测' })).toBeVisible()
  })

  it('MCP 与连接页签承载连接维护入口', async () => {
    const bridge = bridgeFixture()
    render(<ModelAgentCenter bridge={bridge} />)
    fireEvent.click(await screen.findByRole('tab', { name: 'MCP 与连接' }))
    expect(await screen.findByText('新增连接')).toBeVisible()
    expect(screen.queryByText('其他 MCP')).toBeNull()
  })

  it('诊断页签展示能力和运行时状态', async () => {
    const bridge = bridgeFixture()
    render(<ModelAgentCenter bridge={bridge} />)
    fireEvent.click(await screen.findByRole('tab', { name: '诊断' }))
    expect(await screen.findByText('读取文件')).toBeVisible()
  })
})
