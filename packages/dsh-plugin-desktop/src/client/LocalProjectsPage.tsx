import { useEffect, useMemo, useState } from 'react'
import type { SessionsLike, WorkspaceListState, WorkspacesLike } from './contracts'
import type { DesktopBridgeLike } from './desktop-bridge'
import { ProjectCard } from './ProjectCard'
import { ProjectDeleteDialog, type ProjectDeleteScope } from './ProjectDeleteDialog'
import { projectCards, type ProjectCoverToken, type ProjectMetadataSnapshot } from './project-model'
import { ProfileSelector } from './ProfileSelector'
import { ProjectComposer } from './ProjectComposer'
import { createProjectController } from './project-controller'

export interface LocalProjectsPageProps {
  state: WorkspaceListState
  workspaces: WorkspacesLike
  sessions: SessionsLike
  bridge: DesktopBridgeLike
  onClose(): void
}

export function LocalProjectsPage({ state, workspaces, sessions, bridge, onClose }: LocalProjectsPageProps) {
  const [metadata, setMetadata] = useState<ProjectMetadataSnapshot>({ schemaVersion: 1, projects: {} })
  const cards = useMemo(() => projectCards(state.items, metadata.projects), [metadata.projects, state.items])
  const controller = useMemo(() => createProjectController(
    workspaces,
    sessions,
    (target) => bridge.request<string>('project.directory.create', { path: target }),
  ), [bridge, workspaces, sessions])
  const [busyId, setBusyId] = useState<string | null>(null)
  const [unavailable, setUnavailable] = useState<ReadonlySet<string>>(() => new Set())
  const [actionError, setActionError] = useState<string | null>(null)
  const [profilePending, setProfilePending] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null)
  const selectedCard = selectedId === null ? undefined : cards.find((card) => card.id === selectedId)

  useEffect(() => {
    if (profilePending) return
    let cancelled = false
    void bridge.request<ProjectMetadataSnapshot>('project.metadata.list').then((snapshot) => {
      if (!cancelled) setMetadata(snapshot)
    }).catch((cause) => {
      if (!cancelled) setActionError(workspaceFailure(cause).message)
    })
    return () => { cancelled = true }
  }, [bridge, profilePending])

  useEffect(() => {
    if (selectedId !== null && !cards.some((card) => card.id === selectedId)) setSelectedId(null)
  }, [cards, selectedId])

  useEffect(() => {
    if (profilePending) {
      setSelectedId(null)
      setDeleteTargetId(null)
    }
  }, [profilePending])

  const open = async (workspaceId: string) => {
    setBusyId(workspaceId)
    setActionError(null)
    try {
      const sessionId = await workspaces.connectWorkspace(workspaceId)
      sessions.open(sessionId)
      onClose()
    } catch (cause) {
      const failure = workspaceFailure(cause)
      if (failure.code === 'workspace-invalid-path' || failure.code === 'workspace-not-found') {
        setUnavailable((current) => new Set(current).add(workspaceId))
      } else {
        setActionError(failure.message)
      }
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (workspaceId: string, scope: ProjectDeleteScope) => {
    setBusyId(workspaceId)
    setActionError(null)
    let recycled = false
    try {
      if (scope === 'recycle') {
        await bridge.request('project.directory.recycle', { workspaceId })
        recycled = true
      }
      await workspaces.delete(workspaceId)
      const snapshot = await bridge.request<ProjectMetadataSnapshot>('project.metadata.remove', { workspaceId })
      if (snapshot?.projects !== undefined) setMetadata(snapshot)
      setDeleteTargetId(null)
      setSelectedId((current) => current === workspaceId ? null : current)
    } catch (cause) {
      if (recycled) {
        throw new Error('目录已进入回收站，但项目注册移除失败，请重试仅移除')
      }
      throw cause
    } finally {
      setBusyId(null)
    }
  }

  const rename = async (workspaceId: string, title: string) => {
    setBusyId(workspaceId)
    setActionError(null)
    try {
      await workspaces.rename(workspaceId, title)
    } catch (cause) {
      setActionError(workspaceFailure(cause).message)
    } finally {
      setBusyId(null)
    }
  }

  const patchMetadata = async (workspaceId: string, patch: { cover?: ProjectCoverToken; pinned?: boolean }) => {
    setBusyId(workspaceId)
    setActionError(null)
    try {
      const snapshot = await bridge.request<ProjectMetadataSnapshot>('project.metadata.patch', { workspaceId, patch })
      setMetadata(snapshot)
    } catch (cause) {
      setActionError(workspaceFailure(cause).message)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <section className="dshDesktopProjectsPage" aria-busy={state.state === 'loading' || profilePending || undefined}>
      <div className="dshDesktopProjectsPageInner">
        <header className="dshDesktopProjectsHeader">
          <div>
            <p>DEEPSEEK HARNESS · LOCAL</p>
            <h1>本地项目</h1>
            <span>项目来自当前 Profile 的 Workspace 列表</span>
          </div>
          <button type="button" aria-label="关闭本地项目" onClick={onClose}>×</button>
        </header>
        <ProfileSelector bridge={bridge} onPendingChange={setProfilePending} />

        {state.state === 'loading' && (
          <div className="dshDesktopProjectSkeletons" aria-label="正在加载本地项目">
            <span /><span /><span />
          </div>
        )}
        {state.state === 'error' && <div className="dshDesktopProjectError" role="alert">{state.error?.message ?? '无法读取本地项目'}</div>}
        {actionError !== null && <div className="dshDesktopProjectError" role="alert">{actionError}</div>}

        {state.state !== 'loading' && state.state !== 'error' && cards.length > 0 && (
          <div className="dshDesktopProjectGrid">
            {cards.map((card, index) => (
              <ProjectCard
                key={card.id}
                card={card}
                selected={selectedId === card.id}
                unavailable={unavailable.has(card.id)}
                recent={index === 0}
                disabled={busyId !== null || profilePending}
                onSelect={() => setSelectedId(card.id)}
                onOpen={() => open(card.id)}
                onRename={(title) => rename(card.id, title)}
                onCoverChange={(cover) => patchMetadata(card.id, { cover })}
                onPinChange={(pinned) => patchMetadata(card.id, { pinned })}
                onDelete={() => setDeleteTargetId(card.id)}
              />
            ))}
          </div>
        )}

        {state.state !== 'loading' && state.state !== 'error' && cards.length === 0 && (
          <div className="dshDesktopProjectEmpty">
            <span className="dshDesktopProjectEmptyIcon" aria-hidden="true">＋</span>
            <h2>还没有本地项目</h2>
            <p>可以通过对话构建你的第一个本地项目</p>
          </div>
        )}

        {state.state !== 'loading' && state.state !== 'error' && (
          <div className="dshDesktopProjectComposerDock">
            <ProjectComposer
              bridge={bridge}
              controller={controller}
              selected={selectedCard}
              disabled={profilePending || busyId !== null}
              onClearSelection={() => setSelectedId(null)}
              onComplete={onClose}
            />
          </div>
        )}
        {deleteTargetId !== null && (() => {
          const card = cards.find((candidate) => candidate.id === deleteTargetId)
          if (card === undefined) return null
          return (
            <ProjectDeleteDialog
              project={{ ...card, unavailable: unavailable.has(card.id) }}
              onConfirm={(scope) => remove(card.id, scope)}
              onCancel={() => closeDeleteDialog(card.id, setDeleteTargetId)}
            />
          )
        })()}
      </div>
    </section>
  )
}

function closeDeleteDialog(workspaceId: string, close: (value: null) => void) {
  close(null)
  queueMicrotask(() => {
    for (const card of document.querySelectorAll<HTMLElement>('[data-project-id]')) {
      if (card.dataset.projectId === workspaceId) {
        card.querySelector<HTMLElement>('.dshDesktopProjectCardSurface')?.focus()
        break
      }
    }
  })
}

function workspaceFailure(cause: unknown): { code: string; message: string } {
  if (typeof cause === 'object' && cause !== null) {
    const candidate = cause as { code?: unknown; message?: unknown; rpcError?: { code?: unknown; message?: unknown } }
    const source = candidate.rpcError ?? candidate
    if (typeof source.code === 'string' || typeof source.message === 'string') {
      return {
        code: typeof source.code === 'string' ? source.code : 'workspace-error',
        message: typeof source.message === 'string' ? source.message : '本地项目操作失败',
      }
    }
  }
  return { code: 'workspace-error', message: cause instanceof Error ? cause.message : '本地项目操作失败' }
}
