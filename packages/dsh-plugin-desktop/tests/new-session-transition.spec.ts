import { describe, expect, it, vi } from 'vitest'
import {
  installNewSessionTransition,
  resolveNewSessionWorkspace,
} from '../src/client/new-session-transition'
import { sessionFixture, workspaceFixture } from './fixtures'

describe('new session transition', () => {
  const currentWorkspace = {
    workspaceId: 'w-current',
    path: 'C:\\current',
    title: '当前',
    sessionIds: ['s-current'],
    createdAt: '2026-08-24T00:00:00Z',
    updatedAt: '2026-08-24T00:00:00Z',
  }
  const recentWorkspace = {
    ...currentWorkspace,
    workspaceId: 'w-recent',
    path: 'C:\\recent',
    title: '最近',
    sessionIds: [],
  }

  it('preserves explicit, current, and recent workspace priority', () => {
    const snapshot = workspaceFixture([recentWorkspace, currentWorkspace]).list.getSnapshot()
    expect(resolveNewSessionWorkspace(snapshot, 's-current', 'w-explicit')).toBe('w-explicit')
    expect(resolveNewSessionWorkspace(snapshot, 's-current')).toBe('w-current')
    expect(resolveNewSessionWorkspace(snapshot, 'missing')).toBe('w-recent')
  })

  it('clears the old session before starting the resolved workspace and restores on dispose', async () => {
    const order: string[] = []
    const workspaces = workspaceFixture([recentWorkspace, currentWorkspace])
    const sessions = sessionFixture('s-current')
    const original = workspaces.startSession
    vi.mocked(workspaces.startSession).mockImplementation((workspaceId) => {
      order.push(`start:${workspaceId ?? 'none'}`)
      sessions.setCurrent('s-new')
    })
    vi.mocked(sessions.clear).mockImplementation(() => { order.push('clear') })

    const dispose = installNewSessionTransition(workspaces, sessions)
    workspaces.startSession()

    expect(order).toEqual(['clear', 'start:w-current'])
    await vi.waitFor(() => {
      expect(workspaces.refresh).toHaveBeenCalledOnce()
      expect(sessions.refresh).toHaveBeenCalledOnce()
    })
    dispose()
    expect(workspaces.startSession).toBe(original)
  })

  it('refreshes the promoted session only when its projection changes', async () => {
    const workspaces = workspaceFixture([recentWorkspace])
    const sessions = sessionFixture('s-current')
    vi.mocked(workspaces.startSession).mockImplementation(() => {
      sessions.setCurrent('s-new')
    })
    const dispose = installNewSessionTransition(workspaces, sessions)

    workspaces.startSession()
    await vi.waitFor(() => expect(sessions.refresh).toHaveBeenCalledOnce())
    sessions.setList({
      ids: ['s-new', 's-current'],
      byId: { 's-new': { blank: true, running: true, updatedAt: 1 } },
      current: 's-new',
    })
    await vi.waitFor(() => expect(sessions.refresh).toHaveBeenCalledTimes(2))
    sessions.setList({ current: 's-new' })
    await new Promise((resolveWait) => setTimeout(resolveWait, 20))
    expect(sessions.refresh).toHaveBeenCalledTimes(2)

    dispose()
  })

  it('shares one wrapper across repeated installations', () => {
    const workspaces = workspaceFixture([recentWorkspace])
    const sessions = sessionFixture()
    const original = workspaces.startSession
    const disposeFirst = installNewSessionTransition(workspaces, sessions)
    const wrapped = workspaces.startSession
    const disposeSecond = installNewSessionTransition(workspaces, sessions)

    expect(workspaces.startSession).toBe(wrapped)
    disposeFirst()
    expect(workspaces.startSession).toBe(wrapped)
    disposeSecond()
    expect(workspaces.startSession).toBe(original)
  })
})
