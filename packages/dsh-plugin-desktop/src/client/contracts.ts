import type { ComponentType, ReactNode } from 'react'
import type { DesktopBridgeLike } from './desktop-bridge'
import type { LocalProjectsState } from './local-projects-state'
import type { PluginCenterState } from './plugin-center-state'

export type DesktopPlatform = 'win32' | 'darwin'

export interface RootSlotDefinition {
  name: string
  children: Record<string, { kind: 'single' | 'list'; scope: 'root' | 'session' | 'session-maybe' }>
  inject: () => Record<string, unknown>
}

export interface SettingsSectionDefinition {
  name: 'settings.section'
  id: string
  order: number
  label: string | (() => string)
  inject?: () => Record<string, unknown>
}

export interface SidebarFooterActionDefinition {
  name: 'sidebar.footer.action'
  id: string
  order: number
  inject: () => Record<string, unknown>
}

export type SlotDefinition = RootSlotDefinition | SettingsSectionDefinition | SidebarFooterActionDefinition

export interface ClientContextLike {
  effect(register: () => void | (() => void), label: string): void
  on?(event: 'theme/change', listener: (snapshot: unknown) => void): () => void
  reflect: { provide(name: string, value: unknown): () => void }
  slots: {
    register(definition: SlotDefinition, component: ComponentType<any>): () => void
    inject?(name: string, setup: () => void | (() => void)): () => void
  }
  theme?: {
    getTheme(): unknown
  }
  workspaces: WorkspacesLike
  sessions: SessionsLike
}

export interface ObservableSnapshot<T> {
  getSnapshot(): T
  subscribe(listener: () => void): () => void
}

export interface WorkspaceView {
  workspaceId: string
  path: string
  title: string
  sessionIds: string[]
  createdAt: string
  updatedAt: string
}

export interface WorkspaceListState {
  items: readonly WorkspaceView[]
  archivedSessionIds: readonly string[]
  state: 'idle' | 'loading' | 'error'
  phase: string
  error: { code: string; message: string } | null
  baselinesReady: boolean
  recentWorkspaceId?: string
}

export interface SessionListStateLike {
  ids: readonly string[]
  byId: Record<string, {
    blank?: boolean
    running?: boolean
    updatedAt?: number
    title?: string
  }>
  current?: string
}

export interface WorkspacesLike {
  readonly list: ObservableSnapshot<WorkspaceListState>
  startSession(workspaceId?: string): void
  refresh(): Promise<void>
  create(input: { path: string }): Promise<WorkspaceView>
  createDirectory(path: string, name: string): Promise<string>
  rename(workspaceId: string, title: string): Promise<void>
  insertBefore(workspaceId: string, beforeWorkspaceId?: string): Promise<readonly string[]>
  delete(workspaceId: string): Promise<void>
  connectWorkspace(workspaceId: string): Promise<string>
}

export interface SessionFaceLike {
  prompt(content: Array<{ type: 'text'; text: string }>, mode: 'queue' | 'steer'): Promise<{
    ok: boolean
    value?: { accepted: true }
    error?: { code: string; message: string }
  }>
}

export interface SessionsLike {
  readonly list: ObservableSnapshot<SessionListStateLike>
  clear(): void
  refresh(): Promise<void>
  create(opts?: { workspaceId?: string; cwd?: string; sessionId?: string }): Promise<string>
  open(id: string): void
  binding(id: string): { sessionId: string; session: SessionFaceLike } | undefined
}

export interface AdvancedFrameProps {
  layout: import('./layout-state').DesktopLayoutState
  platform: DesktopPlatform
  renderSlot(name: 'sidebar' | 'conversation' | 'details' | 'shell.overlay', props: Record<string, unknown>): ReactNode
  useSessions<T>(selector: (state: { current?: string; byId: Record<string, { blank?: boolean }> }) => T): T
  useWorkspaces<T>(selector: (state: WorkspaceListState) => T): T
  workspaces: WorkspacesLike
  sessions: SessionsLike
  bridge: DesktopBridgeLike
  localProjects: LocalProjectsState
  pluginCenter: PluginCenterState
}
