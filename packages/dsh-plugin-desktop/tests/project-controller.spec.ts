import { describe, expect, it, vi } from 'vitest'
import { createProjectController } from '../src/client/project-controller'
import { sessionFixture, workspaceFixture } from './fixtures'

describe('project controller', () => {
  const locationGateway = () => ({
    preview: vi.fn(async () => ({
      projectName: '记账应用',
      suggestedPath: 'C:\\Users\\test\\Documents\\DeepSeek Harness\\Projects\\记账应用',
    })),
    create: vi.fn(async () => 'C:\\Users\\test\\Documents\\DeepSeek Harness\\Projects\\记账应用'),
  })

  it('previews through the backend without touching workspace or session services', async () => {
    const workspaces = workspaceFixture()
    const sessions = sessionFixture()
    const locations = locationGateway()
    const controller = createProjectController(workspaces, sessions, locations)

    const draft = await controller.prepare({ idea: '做一个记账应用', profileId: 'p-a' })

    expect(locations.preview).toHaveBeenCalledWith('做一个记账应用')
    expect(draft.proposedName).toBe('记账应用')
    expect(workspaces.create).not.toHaveBeenCalled()
    expect(workspaces.createDirectory).not.toHaveBeenCalled()
    expect(sessions.create).not.toHaveBeenCalled()
  })

  it('creates the workspace, queues the idea, and opens the session after confirmation', async () => {
    const workspaces = workspaceFixture()
    const sessions = sessionFixture()
    const locations = locationGateway()
    const controller = createProjectController(workspaces, sessions, locations)
    const draft = await controller.prepare({ idea: '做一个记账应用', profileId: 'p-a' })

    await controller.confirm(draft)

    expect(locations.create).toHaveBeenCalledWith('记账应用')
    expect(workspaces.create).toHaveBeenCalledWith({ path: 'C:\\Users\\test\\Documents\\DeepSeek Harness\\Projects\\记账应用' })
    expect(sessions.create).toHaveBeenCalledWith({
      workspaceId: 'w-new',
      cwd: 'C:\\Users\\test\\Documents\\DeepSeek Harness\\Projects\\记账应用',
    })
    expect(sessions.session.prompt).toHaveBeenCalledWith([
      { type: 'text', text: expect.stringContaining('做一个记账应用') },
    ], 'queue')
    expect(sessions.open).toHaveBeenCalledWith('s-1')
    expect(vi.mocked(sessions.create).mock.invocationCallOrder[0])
      .toBeLessThan(vi.mocked(sessions.binding).mock.invocationCallOrder[0])
    expect(vi.mocked(sessions.binding).mock.invocationCallOrder[0])
      .toBeLessThan(sessions.session.prompt.mock.invocationCallOrder[0])
    expect(sessions.session.prompt.mock.invocationCallOrder[0])
      .toBeLessThan(vi.mocked(sessions.open).mock.invocationCallOrder[0])
  })

  it('keeps the created directory and rolls back only registration when prompt fails', async () => {
    const workspaces = workspaceFixture()
    const sessions = sessionFixture()
    sessions.session.prompt.mockResolvedValue({ ok: false, error: { code: 'rejected', message: '无法排队' } })
    const locations = locationGateway()
    const controller = createProjectController(workspaces, sessions, locations)
    const draft = await controller.prepare({ idea: '构建工具', profileId: 'p-a' })

    await expect(controller.confirm(draft)).rejects.toThrow('无法排队')

    expect(locations.create).toHaveBeenCalledOnce()
    expect(workspaces.createDirectory).not.toHaveBeenCalled()
    expect(workspaces.delete).toHaveBeenCalledWith('w-new')
    expect(sessions.open).not.toHaveBeenCalled()
  })

  it('rolls back registration when no session binding is available', async () => {
    const workspaces = workspaceFixture()
    const sessions = sessionFixture()
    vi.mocked(sessions.binding).mockReturnValue(undefined)
    const controller = createProjectController(workspaces, sessions, locationGateway())
    const draft = await controller.prepare({ idea: '构建工具', profileId: 'p-a' })

    await expect(controller.confirm(draft)).rejects.toThrow('会话尚未准备好')
    expect(workspaces.delete).toHaveBeenCalledWith('w-new')
  })

  it('fails immediately when create resolves without a synchronous session binding', async () => {
    const workspaces = workspaceFixture()
    const sessions = sessionFixture()
    vi.mocked(sessions.binding)
      .mockReturnValueOnce(undefined)
      .mockReturnValue({ sessionId: 's-1', session: sessions.session })
    const controller = createProjectController(workspaces, sessions, locationGateway())
    const draft = await controller.prepare({ idea: '构建工具', profileId: 'p-a' })

    await expect(controller.confirm(draft)).rejects.toThrow('会话尚未准备好')

    expect(sessions.binding).toHaveBeenCalledTimes(1)
    expect(sessions.session.prompt).not.toHaveBeenCalled()
    expect(sessions.open).not.toHaveBeenCalled()
    expect(workspaces.delete).toHaveBeenCalledWith('w-new')
  })

  it('queues a modification in the selected workspace without creating another workspace', async () => {
    const workspaces = workspaceFixture()
    const sessions = sessionFixture()
    const controller = createProjectController(workspaces, sessions, locationGateway())

    await controller.modify('w-1', '  把首页改成两栏  ')

    expect(workspaces.connectWorkspace).toHaveBeenCalledWith('w-1')
    expect(workspaces.create).not.toHaveBeenCalled()
    expect(sessions.session.prompt).toHaveBeenCalledWith([
      { type: 'text', text: expect.stringContaining('把首页改成两栏') },
    ], 'queue')
    expect(sessions.open).toHaveBeenCalledWith('s-1')
  })

  it('does not create a duplicate session when connectWorkspace violates the binding contract', async () => {
    const workspaces = workspaceFixture()
    const sessions = sessionFixture()
    vi.mocked(sessions.binding).mockReturnValue(undefined)
    const controller = createProjectController(workspaces, sessions, locationGateway())

    await expect(controller.modify('w-1', '更新首页')).rejects.toThrow('会话尚未准备好')

    expect(workspaces.connectWorkspace).toHaveBeenCalledWith('w-1')
    expect(sessions.binding).toHaveBeenCalledTimes(1)
    expect(sessions.create).not.toHaveBeenCalled()
    expect(sessions.session.prompt).not.toHaveBeenCalled()
    expect(sessions.open).not.toHaveBeenCalled()
  })

  it('rejects an empty modification before connecting the workspace', async () => {
    const workspaces = workspaceFixture()
    const controller = createProjectController(workspaces, sessionFixture(), locationGateway())
    await expect(controller.modify('w-1', '   ')).rejects.toThrow('修改需求')
    expect(workspaces.connectWorkspace).not.toHaveBeenCalled()
  })
})
