import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ProjectComposer } from '../src/client/ProjectComposer'
import { createProjectController } from '../src/client/project-controller'
import { bridgeFixture, sessionFixture, workspaceFixture } from './fixtures'

describe('project composer', () => {
  const composerSetup = () => {
    const workspaces = workspaceFixture()
    const sessions = sessionFixture()
    const bridge = bridgeFixture({
      'project.directory.preview': {
        projectName: '记账应用',
        suggestedPath: 'C:\\Users\\test\\Documents\\DeepSeek Harness\\Projects\\记账应用',
      },
      'project.directory.create': 'C:\\Users\\test\\Documents\\DeepSeek Harness\\Projects\\记账应用',
    })
    const locations = {
      preview: (idea: string) => bridge.request<{ projectName: string; suggestedPath: string }>('project.directory.preview', { idea }),
      create: (projectName: string) => bridge.request<string>('project.directory.create', { projectName }),
    }
    return { bridge, workspaces, sessions, controller: createProjectController(workspaces, sessions, locations) }
  }

  it('shows only the requirement input and read-only generated preview', async () => {
    const { bridge, workspaces, controller } = composerSetup()
    render(<ProjectComposer bridge={bridge} controller={controller} onComplete={vi.fn()} />)

    expect(screen.queryByLabelText('项目路径')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('构建 Profile')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('构建权限模式')).not.toBeInTheDocument()
    expect(screen.queryByText('目录尚不存在，需要创建')).not.toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('项目需求'), { target: { value: '做一个记账应用' } })
    const preview = screen.getByRole('button', { name: '检查并预览' })
    await waitFor(() => expect(preview).toBeEnabled())
    fireEvent.click(preview)

    expect(await screen.findByRole('heading', { name: '确认构建范围' })).toBeInTheDocument()
    expect(screen.getByText('记账应用')).toBeVisible()
    expect(screen.getByText(/Documents.*DeepSeek Harness.*Projects.*记账应用/)).toBeVisible()
    expect(screen.queryByText('package-manager · build · test')).not.toBeInTheDocument()
    expect(workspaces.create).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '返回修改' }))
    expect(workspaces.create).not.toHaveBeenCalled()
  })

  it('starts the real session only after explicit confirmation', async () => {
    const { bridge, sessions, controller } = composerSetup()
    const onComplete = vi.fn()
    render(<ProjectComposer bridge={bridge} controller={controller} onComplete={onComplete} />)
    fireEvent.change(screen.getByLabelText('项目需求'), { target: { value: '做一个记账应用' } })
    const preview = screen.getByRole('button', { name: '检查并预览' })
    await waitFor(() => expect(preview).toBeEnabled())
    fireEvent.click(preview)

    fireEvent.click(await screen.findByRole('button', { name: '确认并开始构建' }))

    await waitFor(() => expect(sessions.session.prompt).toHaveBeenCalled())
    expect(onComplete).toHaveBeenCalledOnce()
  })

  it('binds modification mode to one selected project and preserves text on failure', async () => {
    const { bridge, controller } = composerSetup()
    controller.modify = vi.fn(async () => { throw new Error('发送失败') })
    render(
      <ProjectComposer
        bridge={bridge}
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
