import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AgentWorkbench } from '../src/client/agent-workbench'
import type { DesktopBridgeLike } from '../src/client/desktop-bridge'

function bridgeFixture(status: string = 'active') {
  const task = { taskId: 'task-1', workerSessionId: 'session-1', generationId: 'generation-1', providerId: 'codex', agentId: 'codex:default', workspaceId: '/workspace', permission: 'request-approval', status }
  const requestV2 = vi.fn(async (action: Parameters<DesktopBridgeLike['requestV2']>[0]) => {
    if (action === 'task.list') return [task]
    if (action === 'approval.list') return [{ approvalId: 'approval-1', taskId: 'task-1', capabilityKind: 'terminal', scope: 'workspace', riskClass: 'medium', status: 'pending' }]
    if (action === 'content-reference.read') return { content: 'diff --git a/a b/a' }
    return task
  })
  const bridge = {
    request: vi.fn(),
    requestV2,
    dispose: vi.fn(),
  } as unknown as DesktopBridgeLike & { requestV2: typeof requestV2 }
  return { bridge, task }
}

describe('AgentWorkbench', () => {
  it('loads tasks, exposes user-selected permission modes, and shows full-access warning', async () => {
    const { bridge } = bridgeFixture()
    render(<AgentWorkbench bridge={bridge} workspaceId="workspace-1" />)

    expect(await screen.findByText('task-1')).toBeInTheDocument()
    expect(bridge.requestV2).toHaveBeenCalledWith('task.list', undefined, { workspaceId: 'workspace-1' })
    fireEvent.change(screen.getByLabelText('权限模式'), { target: { value: 'full-access' } })
    expect(screen.getByText(/完全访问权限会允许 Agent/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('任务提示'), { target: { value: '检查项目' } })
    fireEvent.click(screen.getByRole('button', { name: '创建任务' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('task.create', undefined, expect.objectContaining({ workspaceId: 'workspace-1', prompt: '检查项目', permission: 'full-access' })))
  })

  it('loads pending approvals and sends an explicit allow-once decision', async () => {
    const { bridge } = bridgeFixture()
    render(<AgentWorkbench bridge={bridge} workspaceId="workspace-1" />)
    fireEvent.click(await screen.findByRole('button', { name: '任务 task-1' }))
    expect(await screen.findByText('terminal')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '允许一次' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('approval.resolve', undefined, { approvalId: 'approval-1', taskId: 'task-1', decision: 'allow-once' }))
  })

  it('offers recovery only for an explicit reviewable task and keeps it non-executing', async () => {
    const active = bridgeFixture()
    const activeView = render(<AgentWorkbench bridge={active.bridge} workspaceId="workspace-1" />)
    fireEvent.click(await screen.findByRole('button', { name: '任务 task-1' }))
    expect(screen.queryByRole('button', { name: '接管待复核任务' })).not.toBeInTheDocument()
    activeView.unmount()

    const reviewable = bridgeFixture('needs-review')
    render(<AgentWorkbench bridge={reviewable.bridge} workspaceId="workspace-1" />)
    fireEvent.click(await screen.findByRole('button', { name: '任务 task-1' }))
    await waitFor(() => expect(reviewable.bridge.requestV2.mock.calls.filter(([action]) => action === 'approval.list')).toHaveLength(1))
    fireEvent.click(await screen.findByRole('button', { name: '接管待复核任务' }))
    await waitFor(() => {
      expect(reviewable.bridge.requestV2).toHaveBeenCalledWith('task.recover', undefined, {
        taskId: 'task-1', workspaceId: '/workspace', sourceSessionId: 'session-1',
      })
      expect(reviewable.bridge.requestV2.mock.calls.filter(([action]) => action === 'approval.list')).toHaveLength(2)
    })
  })
})
