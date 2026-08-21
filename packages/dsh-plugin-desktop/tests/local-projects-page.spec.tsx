import { fireEvent, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { bridgeFixture, renderFrame, sessionFixture, workspaceFixture } from './fixtures'

describe('local projects page', () => {
  it('opens a workspace session and returns to the conversation', async () => {
    const workspaces = workspaceFixture([{
      workspaceId: 'w-1', path: 'C:\\code\\demo', title: 'demo', sessionIds: [],
      createdAt: '2026-08-19T00:00:00Z', updatedAt: '2026-08-19T00:00:00Z',
    }])
    const sessions = sessionFixture()
    renderFrame({ workspaces, sessions })
    fireEvent.click(screen.getByRole('button', { name: '本地项目' }))
    fireEvent.doubleClick(screen.getByRole('button', { name: '项目 demo' }))

    await waitFor(() => expect(workspaces.connectWorkspace).toHaveBeenCalledWith('w-1'))
    expect(sessions.open).toHaveBeenCalledWith('s-1')
    expect(screen.queryByRole('region', { name: '本地项目' })).not.toBeInTheDocument()
  })

  it('binds the composer to one selected project without opening it', async () => {
    const workspaces = workspaceFixture([{
      workspaceId: 'w-1', path: 'C:\\code\\demo', title: 'demo', sessionIds: [],
      createdAt: '2026-08-19T00:00:00Z', updatedAt: '2026-08-19T00:00:00Z',
    }])
    const sessions = sessionFixture()
    renderFrame({ workspaces, sessions })
    fireEvent.click(screen.getByRole('button', { name: '本地项目' }))
    const card = screen.getByRole('button', { name: '项目 demo' })
    await waitFor(() => expect(card).not.toHaveAttribute('aria-disabled'))
    fireEvent.click(card)

    expect(screen.getByText('正在修改 demo')).toBeVisible()
    expect(workspaces.connectWorkspace).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('修改需求'), { target: { value: '把首页改成两栏' } })
    fireEvent.click(screen.getByRole('button', { name: '发送修改' }))
    await waitFor(() => expect(sessions.session.prompt).toHaveBeenCalledWith(
      [{ type: 'text', text: expect.stringContaining('把首页改成两栏') }], 'queue',
    ))
  })

  it('opens with Enter and confirms deletion with Delete', async () => {
    const workspaces = workspaceFixture([{
      workspaceId: 'w-1', path: 'C:\\code\\demo', title: 'demo', sessionIds: [],
      createdAt: '2026-08-19T00:00:00Z', updatedAt: '2026-08-19T00:00:00Z',
    }])
    renderFrame({ workspaces })
    fireEvent.click(screen.getByRole('button', { name: '本地项目' }))
    const card = screen.getByRole('button', { name: '项目 demo' })
    await waitFor(() => expect(card).not.toHaveAttribute('aria-disabled'))
    fireEvent.keyDown(card, { key: 'Delete' })
    expect(screen.getByRole('dialog', { name: '删除 demo' })).toBeVisible()
    fireEvent.keyDown(screen.getByRole('dialog', { name: '删除 demo' }), { key: 'Escape' })
    fireEvent.keyDown(card, { key: 'Enter' })
    expect(workspaces.connectWorkspace).toHaveBeenCalledWith('w-1')
  })

  it('unregisters a card without deleting its directory', async () => {
    const workspaces = workspaceFixture([{
      workspaceId: 'w-1', path: 'C:\\code\\demo', title: 'demo', sessionIds: [],
      createdAt: '2026-08-19T00:00:00Z', updatedAt: '2026-08-19T00:00:00Z',
    }])
    renderFrame({ workspaces })
    fireEvent.click(screen.getByRole('button', { name: '本地项目' }))
    const card = screen.getByRole('button', { name: '项目 demo' })
    await waitFor(() => expect(card).not.toHaveAttribute('aria-disabled'))
    fireEvent.contextMenu(card)
    fireEvent.click(screen.getByRole('menuitem', { name: '删除项目' }))
    expect(screen.getByRole('radio', { name: '仅从列表移除' })).toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: '确认移除' }))

    await waitFor(() => expect(workspaces.delete).toHaveBeenCalledWith('w-1'))
  })

  it('does not unregister when moving the directory to recycle bin fails', async () => {
    const workspaces = workspaceFixture([{
      workspaceId: 'w-1', path: 'C:\\code\\demo', title: 'demo', sessionIds: [],
      createdAt: '2026-08-19T00:00:00Z', updatedAt: '2026-08-19T00:00:00Z',
    }])
    const bridge = bridgeFixture()
    vi.mocked(bridge.request).mockImplementation(async (action) => {
      if (action === 'profile.list') return {
        selectedProfileId: 'p-default', pendingProfileId: null, lastKnownGoodProfileId: 'p-default',
        profiles: [{ id: 'p-default', name: '默认', revision: 1, status: 'active' }],
      }
      if (action === 'project.metadata.list') return { schemaVersion: 1, projects: {} }
      if (action === 'project.directory.recycle') throw new Error('回收站不可用')
      return undefined
    })
    renderFrame({ workspaces, bridge })
    fireEvent.click(screen.getByRole('button', { name: '本地项目' }))
    const card = screen.getByRole('button', { name: '项目 demo' })
    await waitFor(() => expect(card).not.toHaveAttribute('aria-disabled'))
    fireEvent.contextMenu(card)
    fireEvent.click(screen.getByRole('menuitem', { name: '删除项目' }))
    fireEvent.click(screen.getByRole('radio', { name: '移到 Windows 回收站' }))
    fireEvent.change(screen.getByLabelText('输入项目名称确认'), { target: { value: 'demo' } })
    fireEvent.click(screen.getByRole('button', { name: '移到回收站' }))

    expect(await screen.findByText('回收站不可用')).toBeVisible()
    expect(workspaces.delete).not.toHaveBeenCalled()
  })

  it('recycles, unregisters, then clears metadata in a fixed order', async () => {
    const workspaces = workspaceFixture([{
      workspaceId: 'w-1', path: 'C:\\code\\demo', title: 'demo', sessionIds: [],
      createdAt: '2026-08-19T00:00:00Z', updatedAt: '2026-08-19T00:00:00Z',
    }])
    const bridge = bridgeFixture()
    const order: string[] = []
    vi.mocked(workspaces.delete).mockImplementation(async () => { order.push('unregister') })
    vi.mocked(bridge.request).mockImplementation(async (action) => {
      if (action === 'profile.list') return {
        selectedProfileId: 'p-default', pendingProfileId: null, lastKnownGoodProfileId: 'p-default',
        profiles: [{ id: 'p-default', name: '默认', revision: 1, status: 'active' }],
      }
      if (action === 'project.metadata.list') return { schemaVersion: 1, projects: {} }
      if (action === 'project.directory.recycle') { order.push('recycle'); return 'C:\\code\\demo' }
      if (action === 'project.metadata.remove') { order.push('metadata'); return { schemaVersion: 1, projects: {} } }
      return undefined
    })
    renderFrame({ workspaces, bridge })
    fireEvent.click(screen.getByRole('button', { name: '本地项目' }))
    const card = screen.getByRole('button', { name: '项目 demo' })
    await waitFor(() => expect(card).not.toHaveAttribute('aria-disabled'))
    fireEvent.contextMenu(card)
    fireEvent.click(screen.getByRole('menuitem', { name: '删除项目' }))
    fireEvent.click(screen.getByRole('radio', { name: '移到 Windows 回收站' }))
    fireEvent.change(screen.getByLabelText('输入项目名称确认'), { target: { value: 'demo' } })
    fireEvent.click(screen.getByRole('button', { name: '移到回收站' }))

    await waitFor(() => expect(order).toEqual(['recycle', 'unregister', 'metadata']))
  })

  it('restores focus to the project card after cancelling deletion', async () => {
    const workspaces = workspaceFixture([{
      workspaceId: 'w-1', path: 'C:\\code\\demo', title: 'demo', sessionIds: [],
      createdAt: '2026-08-19T00:00:00Z', updatedAt: '2026-08-19T00:00:00Z',
    }])
    renderFrame({ workspaces })
    fireEvent.click(screen.getByRole('button', { name: '本地项目' }))
    const card = screen.getByRole('button', { name: '项目 demo' })
    await waitFor(() => expect(card).not.toHaveAttribute('aria-disabled'))
    fireEvent.contextMenu(card)
    fireEvent.click(screen.getByRole('menuitem', { name: '删除项目' }))
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' })

    await waitFor(() => expect(card).toHaveFocus())
  })

  it('marks a project whose registered path is unavailable', async () => {
    const workspaces = workspaceFixture([{
      workspaceId: 'w-1', path: 'C:\\code\\missing', title: 'missing', sessionIds: [],
      createdAt: '2026-08-19T00:00:00Z', updatedAt: '2026-08-19T00:00:00Z',
    }])
    vi.mocked(workspaces.connectWorkspace).mockRejectedValue({
      rpcError: { code: 'workspace-invalid-path', message: '目录不存在' },
    })
    renderFrame({ workspaces })
    fireEvent.click(screen.getByRole('button', { name: '本地项目' }))
    fireEvent.doubleClick(screen.getByRole('button', { name: '项目 missing' }))

    expect(await screen.findByText('路径不可用')).toBeInTheDocument()
  })

  it('uses the conversation composer as the empty-state invitation', () => {
    renderFrame({ workspaces: workspaceFixture() })
    fireEvent.click(screen.getByRole('button', { name: '本地项目' }))

    expect(screen.getByRole('heading', { name: '还没有本地项目' })).toBeInTheDocument()
    expect(screen.getByText('可以通过对话构建你的第一个本地项目')).toBeInTheDocument()
    const page = screen.getByRole('region', { name: '本地项目' })
    expect(page.querySelector('.dshDesktopProjectCreatePanel')).toBeNull()
    expect(page.querySelectorAll('.dshDesktopProjectComposer')).toHaveLength(1)
  })

  it('renders one flat composer when projects already exist', () => {
    const workspaces = workspaceFixture([{
      workspaceId: 'w-1', path: 'C:\\code\\demo', title: 'demo', sessionIds: [],
      createdAt: '2026-08-19T00:00:00Z', updatedAt: '2026-08-19T00:00:00Z',
    }])
    renderFrame({ workspaces })
    fireEvent.click(screen.getByRole('button', { name: '本地项目' }))

    const page = screen.getByRole('region', { name: '本地项目' })
    expect(page.querySelector('.dshDesktopProjectCreatePanel')).toBeNull()
    expect(page.querySelectorAll('.dshDesktopProjectComposer')).toHaveLength(1)
  })

  it('locks project actions while a profile generation is switching', async () => {
    const workspaces = workspaceFixture([{
      workspaceId: 'w-1', path: 'C:\\code\\demo', title: 'demo', sessionIds: [],
      createdAt: '2026-08-19T00:00:00Z', updatedAt: '2026-08-19T00:00:00Z',
    }])
    const bridge = bridgeFixture()
    vi.mocked(bridge.request).mockImplementation(async (action) => {
      if (action === 'profile.list') return {
        selectedProfileId: 'p-a', pendingProfileId: 'p-b', lastKnownGoodProfileId: 'p-a',
        profiles: [{ id: 'p-a', name: 'A', revision: 1 }, { id: 'p-b', name: 'B', revision: 1 }],
      }
      if (action === 'project.metadata.list') return { schemaVersion: 1, projects: {} }
      return undefined
    })
    renderFrame({ workspaces, bridge })
    fireEvent.click(screen.getByRole('button', { name: '本地项目' }))

    await waitFor(() => expect(screen.getByRole('button', { name: '项目 demo' })).toHaveAttribute('aria-disabled', 'true'))
    expect(screen.getByRole('button', { name: '项目 demo' })).toHaveAttribute('tabindex', '-1')
    expect(screen.getByRole('region', { name: '本地项目' })).toHaveAttribute('aria-busy', 'true')
  })
})
