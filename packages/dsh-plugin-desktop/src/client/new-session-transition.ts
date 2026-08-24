import type {
  SessionListStateLike,
  SessionsLike,
  WorkspaceListState,
  WorkspacesLike,
} from './contracts'

interface Installation {
  references: number
  original: WorkspacesLike['startSession']
  wrapped: WorkspacesLike['startSession']
  stopReconcile?: () => void
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
  let installation: Installation
  const wrapped: WorkspacesLike['startSession'] = (requestedWorkspaceId) => {
    installation.stopReconcile?.()
    installation.stopReconcile = undefined
    const target = resolveNewSessionWorkspace(
      workspaces.list.getSnapshot(),
      sessions.list.getSnapshot().current,
      requestedWorkspaceId,
    )
    sessions.clear()
    if (target !== undefined) {
      const stop = reconcileNewSessionProjection(workspaces, sessions, () => {
        if (installation.stopReconcile === stop) installation.stopReconcile = undefined
      })
      installation.stopReconcile = stop
    }
    original.call(workspaces, target)
  }
  installation = {
    references: 1,
    original,
    wrapped,
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
  installation.stopReconcile?.()
  installations.delete(workspaces)
}

function reconcileNewSessionProjection(
  workspaces: WorkspacesLike,
  sessions: SessionsLike,
  onDone: () => void,
): () => void {
  let disposed = false
  let promotedSessionId: string | undefined
  let lastSignature: string | undefined
  let refreshing = false
  let unsubscribe: () => void = () => undefined

  const stop = () => {
    if (disposed) return
    disposed = true
    unsubscribe()
    onDone()
  }
  const refresh = () => {
    if (disposed || refreshing) return
    refreshing = true
    void Promise.allSettled([
      Promise.resolve().then(() => workspaces.refresh()),
      Promise.resolve().then(() => sessions.refresh()),
    ]).finally(() => {
      refreshing = false
      observe()
    })
  }
  const observe = () => {
    if (disposed) return
    const snapshot = sessions.list.getSnapshot()
    const current = snapshot.current
    if (current === undefined) return
    if (promotedSessionId === undefined) {
      promotedSessionId = current
      lastSignature = sessionProjectionSignature(snapshot.byId[current])
      refresh()
      return
    }
    if (current !== promotedSessionId) {
      stop()
      return
    }
    const summary = snapshot.byId[current]
    const signature = sessionProjectionSignature(summary)
    if (summary?.blank === false && (summary.title?.trim() ?? '') !== '') {
      stop()
      return
    }
    if (signature === lastSignature) return
    lastSignature = signature
    refresh()
  }
  unsubscribe = sessions.list.subscribe(observe)
  observe()
  return stop
}

function sessionProjectionSignature(summary: SessionListStateLike['byId'][string] | undefined): string {
  if (summary === undefined) return 'missing'
  return JSON.stringify([summary.blank, summary.running, summary.updatedAt, summary.title])
}
