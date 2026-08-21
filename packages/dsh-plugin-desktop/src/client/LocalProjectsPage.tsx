import { useEffect, useMemo, useState } from 'react'
import type { SessionsLike, WorkspaceListState, WorkspacesLike } from './contracts'
import type { DesktopBridgeLike } from './desktop-bridge'
import { AdoptProjectDialog } from './AdoptProjectDialog'
import { ProjectCard } from './ProjectCard'
import { ProjectDeleteDialog, type ProjectDeleteScope } from './ProjectDeleteDialog'
import { projectCards, type ProjectCoverToken, type ProjectMetadataSnapshot } from './project-model'
import type { ProfileListResult } from './profile-model'
import { ProjectComposer } from './ProjectComposer'
import { createProjectController } from './project-controller'

export interface LocalProjectsPageProps {
  state: WorkspaceListState
  workspaces: WorkspacesLike
  sessions: SessionsLike
  bridge: DesktopBridgeLike
  onClose(): void
}

// 桌面桥 app.status 的载荷：本地应用运行态，仅驱动卡片角标与"项目根目录内"的收录过滤。
interface AppsStatus {
  projectsRoot: string
  running: Array<{ workspaceId: string; origin: string; title: string; startedAt: string }>
  launchable: string[]
}

export function LocalProjectsPage({ state, workspaces, sessions, bridge, onClose }: LocalProjectsPageProps) {
  const [metadata, setMetadata] = useState<ProjectMetadataSnapshot>({ schemaVersion: 1, projects: {} })
  const cards = useMemo(() => projectCards(state.items, metadata.projects), [metadata.projects, state.items])
  const [apps, setApps] = useState<AppsStatus | null>(null)
  // 仅展示位于本地项目根目录下、或已通过 localApp 元数据显式收录的项目。
  const visibleCards = useMemo(() => {
    if (apps === null) return []
    const root = normalizeDir(apps.projectsRoot)
    return cards.filter((card) => isUnderRoot(normalizeDir(card.path), root) || metadata.projects[card.id]?.localApp === true)
  }, [apps, cards, metadata])
  const controller = useMemo(() => createProjectController(
    workspaces,
    sessions,
    {
      preview: (idea) => bridge.request('project.directory.preview', { idea }),
      create: (projectName) => bridge.request('project.directory.create', { projectName }),
    },
  ), [bridge, workspaces, sessions])
  const [busyId, setBusyId] = useState<string | null>(null)
  const [unavailable, setUnavailable] = useState<ReadonlySet<string>>(() => new Set())
  const [actionError, setActionError] = useState<string | null>(null)
  const [profilePending, setProfilePending] = useState(false)
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [deleteTargetId, setDeleteTargetId] = useState<string | null>(null)
  const [adoptOpen, setAdoptOpen] = useState(false)
  // 收录候选 = 当前 Profile 中既不在项目根目录下、也没有 localApp 标记的工作区（即未出现在 visibleCards 里的）。
  const adoptable = useMemo(() => state.state !== 'loading' && apps !== null
    ? state.items
        .filter((workspace) => !visibleCards.some((card) => card.id === workspace.workspaceId))
        .map((workspace) => ({ id: workspace.workspaceId, title: workspace.title, path: workspace.path }))
    : [], [state.state, state.items, visibleCards, apps])
  const selectedCard = selectedId === null ? undefined : visibleCards.find((card) => card.id === selectedId)

  const refreshApps = async () => {
    try {
      setApps(await bridge.request<AppsStatus>('app.status'))
    } catch {
      // 状态失败只影响角标与过滤，不阻塞本地项目页其余操作。
    }
  }

  useEffect(() => {
    void refreshApps()
  }, [bridge])

  useEffect(() => {
    if (selectedId !== null && !visibleCards.some((card) => card.id === selectedId)) setSelectedId(null)
  }, [visibleCards, selectedId])

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

  // 页内不再提供 Profile 切换入口；仅在宿主侧正在切换 Profile 时继续停用项目操作。
  useEffect(() => {
    let cancelled = false
    void bridge.request<ProfileListResult>('profile.list').then((result) => {
      if (!cancelled) setProfilePending(result?.pendingProfileId != null)
    }).catch(() => undefined)
    return () => { cancelled = true }
  }, [bridge])

  useEffect(() => {
    if (profilePending) {
      setSelectedId(null)
      setDeleteTargetId(null)
      setAdoptOpen(false)
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

  // 可运行的项目双击/回车即启动本地应用，而不是打开新的会话。
  const launch = async (workspaceId: string) => {
    setBusyId(workspaceId)
    setActionError(null)
    try {
      await bridge.request('app.launch', { workspaceId })
      onClose()
    } catch (cause) {
      setActionError(workspaceFailure(cause).message)
    } finally {
      setBusyId(null)
      void refreshApps()
    }
  }

  const stopApp = async (workspaceId: string) => {
    setBusyId(workspaceId)
    setActionError(null)
    try {
      await bridge.request('app.stop', { workspaceId })
    } catch (cause) {
      setActionError(workspaceFailure(cause).message)
    } finally {
      setBusyId(null)
      void refreshApps()
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

  // 收录根目录之外的工作区：只补写 localApp 元数据标记，不移动目录、不新建会话。
  const adopt = async (workspaceId: string) => {
    setBusyId(workspaceId)
    setActionError(null)
    try {
      const snapshot = await bridge.request<ProjectMetadataSnapshot>('project.metadata.patch', { workspaceId, patch: { localApp: true } })
      if (snapshot?.projects !== undefined) setMetadata(snapshot)
      setAdoptOpen(false)
    } catch (cause) {
      setActionError(workspaceFailure(cause).message)
    } finally {
      setBusyId(null)
    }
  }

  return (
    <section className="dshDesktopProjectsPage" aria-label="本地项目" aria-busy={state.state === 'loading' || profilePending || undefined}>
      <div className="dshDesktopProjectsPageInner">
        {state.state === 'loading' && (
          <div className="dshDesktopProjectSkeletons" aria-label="正在加载本地项目">
            <span /><span /><span />
          </div>
        )}
        {state.state === 'error' && <div className="dshDesktopProjectError" role="alert">{state.error?.message ?? '无法读取本地项目'}</div>}
        {actionError !== null && <div className="dshDesktopProjectError" role="alert">{actionError}</div>}

        {state.state !== 'loading' && state.state !== 'error' && visibleCards.length > 0 && (
          <div className="dshDesktopProjectGrid">
            {visibleCards.map((card, index) => (
              <ProjectCard
                key={card.id}
                card={card}
                selected={selectedId === card.id}
                unavailable={unavailable.has(card.id)}
                recent={index === 0}
                disabled={busyId !== null || profilePending}
                launchable={apps?.launchable.includes(card.id) ?? false}
                running={apps?.running.some((entry) => entry.workspaceId === card.id) ?? false}
                onSelect={() => setSelectedId(card.id)}
                onOpen={() => (apps?.launchable.includes(card.id) ?? false) ? launch(card.id) : open(card.id)}
                onOpenSession={() => open(card.id)}
                onStopApp={() => stopApp(card.id)}
                onRename={(title) => rename(card.id, title)}
                onCoverChange={(cover) => patchMetadata(card.id, { cover })}
                onPinChange={(pinned) => patchMetadata(card.id, { pinned })}
                onDelete={() => setDeleteTargetId(card.id)}
              />
            ))}
          </div>
        )}

        {state.state !== 'loading' && state.state !== 'error' && visibleCards.length === 0 && (
          <div className="dshDesktopProjectEmpty">
            <span className="dshDesktopProjectEmptyIcon" aria-hidden="true">＋</span>
            <h2>还没有本地项目</h2>
            <p>可以通过对话构建你的第一个本地项目</p>
          </div>
        )}

        {state.state !== 'loading' && state.state !== 'error' && (
          <div className="dshDesktopProjectComposerDock">
            <div className="dshDesktopAdoptRow">
              <button type="button" className="dshDesktopAdoptButton" disabled={profilePending || busyId !== null} onClick={() => setAdoptOpen(true)}>收录已有项目</button>
            </div>
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
          const card = visibleCards.find((candidate) => candidate.id === deleteTargetId)
          if (card === undefined) return null
          return (
            <ProjectDeleteDialog
              project={{ ...card, unavailable: unavailable.has(card.id) }}
              onConfirm={(scope) => remove(card.id, scope)}
              onCancel={() => closeDeleteDialog(card.id, setDeleteTargetId)}
            />
          )
        })()}
        {adoptOpen && (
          <AdoptProjectDialog
            candidates={adoptable}
            busy={busyId !== null}
            onAdopt={adopt}
            onClose={() => setAdoptOpen(false)}
          />
        )}
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

// 路径统一成正斜杠、去尾部斜杠并小写，避免 Windows 大小写与分隔符差异误判收录关系。
function normalizeDir(path: string): string {
  return path.replace(/\\/g, '/').replace(/\/+$/g, '').toLowerCase()
}

function isUnderRoot(dir: string, root: string): boolean {
  return dir === root || dir.startsWith(`${root}/`)
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
