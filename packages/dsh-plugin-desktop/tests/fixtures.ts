import { createElement, useSyncExternalStore } from 'react'
import { render } from '@testing-library/react'
import { vi } from 'vitest'
import { AdvancedFrame } from '../src/client/AdvancedFrame'
import type {
  AdvancedFrameProps, ClientContextLike, SessionFaceLike, SessionListStateLike, SessionsLike,
  WorkspaceListState, WorkspacesLike, WorkspaceView,
} from '../src/client/contracts'
import type { DesktopBridgeAction, DesktopBridgeLike } from '../src/client/desktop-bridge'
import { DesktopLayoutState } from '../src/client/layout-state'
import { LocalProjectsState } from '../src/client/local-projects-state'
import { PluginCenterState } from '../src/client/plugin-center-state'
import { LocalProjectsFooterAction } from '../src/client/LocalProjectsFooterAction'

export function workspaceFixture(items: WorkspaceView[] = []) {
  let snapshot: WorkspaceListState = {
    items,
    archivedSessionIds: [],
    state: 'idle',
    phase: 'ready',
    error: null,
    baselinesReady: true,
    recentWorkspaceId: items[0]?.workspaceId,
  }
  const listeners = new Set<() => void>()
  const workspaces: WorkspacesLike & { setList(next: Partial<WorkspaceListState>): void } = {
    list: {
      getSnapshot: () => snapshot,
      subscribe: (listener) => { listeners.add(listener); return () => listeners.delete(listener) },
    },
    startSession: vi.fn(),
    refresh: vi.fn(async () => undefined),
    create: vi.fn(async ({ path }) => ({
      workspaceId: 'w-new', path, title: path.split(/[\\/]/).at(-1) ?? path,
      sessionIds: [], createdAt: '2026-08-19T00:00:00Z', updatedAt: '2026-08-19T00:00:00Z',
    })),
    createDirectory: vi.fn(async (path, name) => `${path}\\${name}`),
    rename: vi.fn(async () => undefined),
    insertBefore: vi.fn(async () => snapshot.items.map((workspace) => workspace.workspaceId)),
    delete: vi.fn(async () => undefined),
    connectWorkspace: vi.fn(async () => 's-1'),
    setList(next) {
      snapshot = { ...snapshot, ...next }
      listeners.forEach((listener) => listener())
    },
  }
  return workspaces
}

export function sessionFixture(current?: string) {
  let snapshot: SessionListStateLike = { ids: current === undefined ? [] : [current], byId: {}, current }
  const listeners = new Set<() => void>()
  const prompt = vi.fn<SessionFaceLike['prompt']>(async () => ({ ok: true, value: { accepted: true as const } }))
  const session = {
    prompt,
  }
  const sessions: SessionsLike & {
    session: typeof session
    setCurrent(current?: string): void
    setList(next: Partial<SessionListStateLike>): void
  } = {
    list: {
      getSnapshot: () => snapshot,
      subscribe: (listener) => { listeners.add(listener); return () => listeners.delete(listener) },
    },
    clear: vi.fn(() => {
      snapshot = { ...snapshot, current: undefined }
      listeners.forEach((listener) => listener())
    }),
    refresh: vi.fn(async () => undefined),
    create: vi.fn(async () => 's-1'),
    open: vi.fn(),
    binding: vi.fn((id) => id === 's-1' ? { sessionId: id, session } : undefined),
    session,
    setCurrent(current) {
      snapshot = { ...snapshot, current }
      listeners.forEach((listener) => listener())
    },
    setList(next) {
      snapshot = { ...snapshot, ...next }
      listeners.forEach((listener) => listener())
    },
  }
  return sessions
}

export function bridgeFixture(responses: Partial<Record<DesktopBridgeAction, unknown>> = {}) {
  const defaultProfiles = {
    selectedProfileId: 'p-default', pendingProfileId: null, lastKnownGoodProfileId: 'p-default',
    profiles: [{ id: 'p-default', name: '默认', revision: 1, status: 'active' }],
  }
  const bridge: DesktopBridgeLike = {
    request: vi.fn(async (action: DesktopBridgeAction) => responses[action]
      ?? (action === 'profile.list' ? defaultProfiles : action === 'project.metadata.list'
        ? { schemaVersion: 1, projects: {} }
        : action === 'app.status'
          ? { projectsRoot: 'C:\\code', running: [], launchable: [] }
          : undefined)) as DesktopBridgeLike['request'],
    requestV2: vi.fn(async () => undefined) as DesktopBridgeLike['requestV2'],
    dispose: vi.fn(),
  }
  return bridge
}

export function contextFixture(overrides: Partial<ClientContextLike> = {}) {
  const context = {
    registeredRoot: undefined as { inject(): Record<string, unknown> } | undefined,
    effect: (setup: () => void | (() => void)) => { setup() },
    reflect: { provide: vi.fn(() => () => undefined) },
    slots: {
      register: vi.fn((definition) => {
        if (definition.name === 'root') context.registeredRoot = definition
        return () => undefined
      }),
    },
    workspaces: workspaceFixture(),
    sessions: sessionFixture(),
    ...overrides,
  }
  return context as typeof context & ClientContextLike
}

export function renderFrame(overrides: Partial<AdvancedFrameProps> = {}) {
  const workspaces = overrides.workspaces ?? workspaceFixture()
  const sessions = overrides.sessions ?? sessionFixture()
  const localProjects = overrides.localProjects ?? new LocalProjectsState()
  const pluginCenter = overrides.pluginCenter ?? new PluginCenterState()
  const props: AdvancedFrameProps = {
    layout: new DesktopLayoutState(),
    platform: 'win32',
    renderSlot: (name, slotProps) => name === 'sidebar'
      ? createElement('div', {},
          createElement('div', { 'data-testid': `${name}-slot` }),
          createElement(LocalProjectsFooterAction, { wide: slotProps.collapsed !== true, state: localProjects }),
        )
      : createElement('div', { 'data-testid': `${name}-slot` }),
    useSessions: (selector) => selector({ byId: {} }),
    useWorkspaces: (selector) => useSyncExternalStore(workspaces.list.subscribe, () => selector(workspaces.list.getSnapshot())),
    workspaces,
    sessions,
    bridge: bridgeFixture(),
    localProjects,
    pluginCenter,
    ...overrides,
  }
  return { ...render(createElement(AdvancedFrame, props)), props, workspaces, sessions }
}
