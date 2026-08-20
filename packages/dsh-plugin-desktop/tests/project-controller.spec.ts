import { describe, expect, it, vi } from 'vitest'
import { createProjectController } from '../src/client/project-controller'
import { sessionFixture, workspaceFixture } from './fixtures'

describe('project controller', () => {
  it('does not touch workspace or session services before confirmation', () => {
    const workspaces = workspaceFixture()
    const sessions = sessionFixture()
    const controller = createProjectController(workspaces, sessions)

    const draft = controller.prepare({ idea: '构建一个博客', path: 'C:\\code\\blog', profileId: 'p-a', permissionMode: 'workspace-write' })

    expect(draft.proposedName).toBe('构建一个博客')
    expect(workspaces.create).not.toHaveBeenCalled()
    expect(workspaces.createDirectory).not.toHaveBeenCalled()
    expect(sessions.create).not.toHaveBeenCalled()
  })

  it('creates the workspace, queues the idea, and opens the session after confirmation', async () => {
    const workspaces = workspaceFixture()
    const sessions = sessionFixture()
    const controller = createProjectController(workspaces, sessions)
    const draft = controller.prepare({ idea: '构建一个博客', path: 'C:\\code\\blog', profileId: 'p-a', permissionMode: 'workspace-write' })

    await controller.confirm(draft)

    expect(workspaces.create).toHaveBeenCalledWith({ path: 'C:\\code\\blog' })
    expect(sessions.create).toHaveBeenCalledWith({ workspaceId: 'w-new' })
    expect(sessions.session.prompt).toHaveBeenCalledWith([
      { type: 'text', text: expect.stringContaining('构建一个博客') },
    ], 'queue')
    expect(sessions.open).toHaveBeenCalledWith('s-1')
  })

  it('creates an explicitly requested directory and rolls back registration when prompt fails', async () => {
    const workspaces = workspaceFixture()
    const sessions = sessionFixture()
    sessions.session.prompt.mockResolvedValue({ ok: false, error: { code: 'rejected', message: '无法排队' } })
    const controller = createProjectController(workspaces, sessions)
    const draft = controller.prepare({
      idea: '构建工具', path: 'C:\\code\\tool', profileId: 'p-a', permissionMode: 'read-only', createDirectory: true,
    })

    await expect(controller.confirm(draft)).rejects.toThrow('无法排队')

    expect(workspaces.createDirectory).toHaveBeenCalledWith('C:\\code', 'tool')
    expect(workspaces.delete).toHaveBeenCalledWith('w-new')
    expect(sessions.open).not.toHaveBeenCalled()
  })

  it('rolls back registration when no session binding is available', async () => {
    const workspaces = workspaceFixture()
    const sessions = sessionFixture()
    vi.mocked(sessions.binding).mockReturnValue(undefined)
    const controller = createProjectController(workspaces, sessions)
    const draft = controller.prepare({ idea: '构建工具', path: 'C:\\code\\tool', profileId: 'p-a', permissionMode: 'workspace-write' })

    await expect(controller.confirm(draft)).rejects.toThrow('会话尚未准备好')
    expect(workspaces.delete).toHaveBeenCalledWith('w-new')
  })

  it('waits for the newly created session binding before queuing the project', async () => {
    const workspaces = workspaceFixture()
    const sessions = sessionFixture()
    vi.mocked(sessions.binding)
      .mockReturnValueOnce(undefined)
      .mockReturnValue({ sessionId: 's-1', session: sessions.session })
    const controller = createProjectController(workspaces, sessions)
    const draft = controller.prepare({ idea: '构建工具', path: 'C:\\code\\tool', profileId: 'p-a', permissionMode: 'workspace-write' })

    await controller.confirm(draft)

    expect(sessions.binding).toHaveBeenCalledTimes(2)
    expect(sessions.session.prompt).toHaveBeenCalled()
    expect(workspaces.delete).not.toHaveBeenCalled()
  })

  it('queues a modification in the selected workspace without creating another workspace', async () => {
    const workspaces = workspaceFixture()
    const sessions = sessionFixture()
    const controller = createProjectController(workspaces, sessions)

    await controller.modify('w-1', '  把首页改成两栏  ')

    expect(workspaces.connectWorkspace).toHaveBeenCalledWith('w-1')
    expect(workspaces.create).not.toHaveBeenCalled()
    expect(sessions.session.prompt).toHaveBeenCalledWith([
      { type: 'text', text: expect.stringContaining('把首页改成两栏') },
    ], 'queue')
    expect(sessions.open).toHaveBeenCalledWith('s-1')
  })

  it('rejects an empty modification before connecting the workspace', async () => {
    const workspaces = workspaceFixture()
    const controller = createProjectController(workspaces, sessionFixture())
    await expect(controller.modify('w-1', '   ')).rejects.toThrow('修改需求')
    expect(workspaces.connectWorkspace).not.toHaveBeenCalled()
  })
})
