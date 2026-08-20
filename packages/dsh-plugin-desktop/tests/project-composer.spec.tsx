import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ProjectComposer } from '../src/client/ProjectComposer'
import { createProjectController } from '../src/client/project-controller'
import { bridgeFixture, sessionFixture, workspaceFixture } from './fixtures'

describe('project composer', () => {
  it('previews path, profile, permission and command categories before writing', async () => {
    const workspaces = workspaceFixture()
    const sessions = sessionFixture()
    render(<ProjectComposer bridge={bridgeFixture()} controller={createProjectController(workspaces, sessions)} onComplete={vi.fn()} />)

    fireEvent.change(screen.getByLabelText('项目需求'), { target: { value: '做一个记账应用' } })
    fireEvent.change(screen.getByLabelText('项目路径'), { target: { value: 'C:\\code\\ledger' } })
    const preview = screen.getByRole('button', { name: '检查并预览' })
    await waitFor(() => expect(preview).toBeEnabled())
    fireEvent.click(preview)

    expect(await screen.findByRole('heading', { name: '确认构建范围' })).toBeInTheDocument()
    expect(screen.getByText('C:\\code\\ledger')).toBeInTheDocument()
    expect(screen.getByText('package-manager · build · test')).toBeInTheDocument()
    expect(workspaces.create).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '返回修改' }))
    expect(workspaces.create).not.toHaveBeenCalled()
  })

  it('starts the real session only after explicit confirmation', async () => {
    const workspaces = workspaceFixture()
    const sessions = sessionFixture()
    const onComplete = vi.fn()
    render(<ProjectComposer bridge={bridgeFixture()} controller={createProjectController(workspaces, sessions)} onComplete={onComplete} />)
    fireEvent.change(screen.getByLabelText('项目需求'), { target: { value: '做一个记账应用' } })
    fireEvent.change(screen.getByLabelText('项目路径'), { target: { value: 'C:\\code\\ledger' } })
    const preview = screen.getByRole('button', { name: '检查并预览' })
    await waitFor(() => expect(preview).toBeEnabled())
    fireEvent.click(preview)

    fireEvent.click(await screen.findByRole('button', { name: '确认并开始构建' }))

    await waitFor(() => expect(sessions.session.prompt).toHaveBeenCalled())
    expect(onComplete).toHaveBeenCalledOnce()
  })

  it('binds modification mode to one selected project and preserves text on failure', async () => {
    const controller = createProjectController(workspaceFixture(), sessionFixture())
    controller.modify = vi.fn(async () => { throw new Error('发送失败') })
    render(
      <ProjectComposer
        bridge={bridgeFixture()}
        controller={controller}
        selected={{
          id: 'w-1', title: 'demo', path: 'C:\\code\\demo', sessionIds: [], pinned: false,
          cover: 'forest', createdAt: '2026-08-20T00:00:00Z', updatedAt: '2026-08-20T00:00:00Z',
        }}
        onComplete={vi.fn()}
      />,
    )

    expect(screen.getByText('正在修改 demo')).toBeVisible()
    fireEvent.change(screen.getByLabelText('修改需求'), { target: { value: '把首页改成两栏' } })
    fireEvent.click(screen.getByRole('button', { name: '发送修改' }))

    expect(await screen.findByText('发送失败')).toBeVisible()
    expect(screen.getByLabelText('修改需求')).toHaveValue('把首页改成两栏')
  })
})
