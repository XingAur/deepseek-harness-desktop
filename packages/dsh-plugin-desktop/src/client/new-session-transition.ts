import type {
  SessionsLike,
  WorkspaceListState,
  WorkspacesLike,
} from './contracts'

interface Installation {
  references: number
  original: WorkspacesLike['startSession']
  wrapped: WorkspacesLike['startSession']
}

const installations = new WeakMap<WorkspacesLike, Installation>()

export function resolveNewSessionWorkspace(
  workspace: WorkspaceListState,
  currentSessionId?: string,
  requestedWorkspaceId?: string,
): string | undefined {
  if (requestedWorkspaceId !== undefined) return requestedWorkspaceId
  if (currentSessionId !== undefined) {
    const current = workspace.items.find((item) => item.sessionIds.includes(currentSessionId))
    if (current !== undefined) return current.workspaceId
  }
  return workspace.recentWorkspaceId
}

export function installNewSessionTransition(
  workspaces: WorkspacesLike,
  sessions: SessionsLike,
): () => void {
  const active = installations.get(workspaces)
  if (active !== undefined) {
    active.references += 1
    return () => release(workspaces, active)
  }

  const original = workspaces.startSession
  const installation: Installation = {
    references: 1,
    original,
    wrapped: (requestedWorkspaceId) => {
      const target = resolveNewSessionWorkspace(
        workspaces.list.getSnapshot(),
        sessions.list.getSnapshot().current,
        requestedWorkspaceId,
      )
      sessions.clear()
      original.call(workspaces, target)
    },
  }
  installations.set(workspaces, installation)
  workspaces.startSession = installation.wrapped
  return () => release(workspaces, installation)
}

function release(workspaces: WorkspacesLike, installation: Installation): void {
  installation.references -= 1
  if (installation.references > 0) return
  if (workspaces.startSession === installation.wrapped) {
    workspaces.startSession = installation.original
  }
  installations.delete(workspaces)
}
