import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import type { AdvancedFrameProps } from './contracts'
import {
  computeDesktopColumns, MACOS_SIDEBAR_COLLAPSED, SIDEBAR_AUTO_COLLAPSE,
  SIDEBAR_COLLAPSED, SIDEBAR_DEFAULT,
} from './layout-state'
import { LocalProjectsPage } from './LocalProjectsPage'
import { AgentHome } from './AgentHome'
import { AgentWorkbench } from './agent-workbench'
import { currentWorkspaceIdOf } from './workspace-selection'

export function AdvancedFrame({ layout, platform, renderSlot, useSessions, useWorkspaces, workspaces, sessions, bridge, localProjects, agentHome }: AdvancedFrameProps) {
  const subscribe = useCallback((listener: () => void) => layout.subscribe(listener), [layout])
  const panels = useSyncExternalStore(subscribe, layout.getSnapshot)
  const frameRef = useRef<HTMLDivElement>(null)
  const [viewport, setViewport] = useState(() => window.innerWidth)
  const projectsOpen = useSyncExternalStore(localProjects.subscribe, localProjects.getSnapshot)
  const agentOpen = useSyncExternalStore(agentHome.subscribe, agentHome.getSnapshot)
  const [agentProvider, setAgentProvider] = useState<string | null>(null)
  const workspaceState = useWorkspaces((state) => state)
  const workspaceList = useWorkspaces((state) => state.items)
  const recentWorkspaceId = useWorkspaces((state) => state.recentWorkspaceId)
  const workspaceId = currentWorkspaceIdOf(workspaceList, recentWorkspaceId)
  const detailsSession = useSessions((state) => {
    const current = state.current
    return current !== undefined && state.byId[current]?.blank === false ? current : undefined
  })

  const openWorkbench = useCallback((providerId: string) => {
    setAgentProvider(providerId)
    agentHome.open()
  }, [agentHome])
  const closeAgentHome = useCallback(() => {
    agentHome.close()
    setAgentProvider(null)
  }, [agentHome])

  useEffect(() => {
    const element = frameRef.current
    if (element === null) return
    const observer = new ResizeObserver(([entry]) => {
      if (entry !== undefined && entry.contentRect.width > 0) setViewport(entry.contentRect.width)
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  const narrow = viewport < SIDEBAR_AUTO_COLLAPSE
  useEffect(() => layout.setNarrow(narrow), [layout, narrow])
  useEffect(() => { if (projectsOpen) layout.closeDetails() }, [layout, projectsOpen])
  useEffect(() => { if (projectsOpen) { agentHome.close(); setAgentProvider(null) } }, [projectsOpen, agentHome])
  useEffect(() => { if (agentOpen) localProjects.close() }, [agentOpen, localProjects])

  const collapsed = panels.narrow ? !panels.narrowExpanded : panels.sidebar === 0
  const sidebarPreference = collapsed ? 0 : panels.sidebar === 0 ? SIDEBAR_DEFAULT : panels.sidebar
  const columns = computeDesktopColumns(
    viewport,
    sidebarPreference,
    projectsOpen || agentOpen || detailsSession === undefined ? 0 : panels.details,
    platform === 'darwin' ? MACOS_SIDEBAR_COLLAPSED : SIDEBAR_COLLAPSED,
  )

  return (
    <div
      ref={frameRef}
      className="dshDesktopFrame"
      data-desktop-platform={platform}
      data-sidebar-collapsed={collapsed || undefined}
      style={{ gridTemplateColumns: `${columns.sidebar}px minmax(0, 1fr) ${columns.details}px` }}
    >
      <aside className="dshDesktopSidebarSurface">
        <div className="dshDesktopUpstreamSidebar">{renderSlot('sidebar', { collapsed, width: columns.sidebar })}</div>
        <div className="dshDesktopSidebarEntries">
          <AgentHomeEntry wide={!collapsed} active={agentOpen} onOpen={() => { if (agentOpen) { closeAgentHome() } else { agentHome.open() } }} />
        </div>
      </aside>
      <main className="dshDesktopConversationSurface">
        {projectsOpen
          ? <LocalProjectsPage state={workspaceState} workspaces={workspaces} sessions={sessions} bridge={bridge} onClose={() => localProjects.close()} />
          : agentOpen
            ? (
              <div className="dshAgentPage">
                {agentProvider === null
                  ? <AgentHome bridge={bridge} workspaceId={workspaceId} onOpenWorkbench={openWorkbench} />
                  : (
                    <div className="dshAgentWorkbenchHost">
                      <header className="dshAgentWorkbenchHostHeader">
                        <button type="button" onClick={() => setAgentProvider(null)}>← 返回 Agent 选择</button>
                        <h3>{agentProvider === 'codex' ? 'Codex' : agentProvider} 工作台</h3>
                      </header>
                      <AgentWorkbench bridge={bridge} workspaceId={workspaceId ?? ''} providerOptions={[{ id: agentProvider, label: agentProvider === 'codex' ? 'Codex' : agentProvider }]} />
                    </div>
                  )}
              </div>
            )
          : renderSlot('conversation', {})}
      </main>
      <aside className="dshDesktopDetailsSurface">{renderSlot('details', {})}</aside>
      <div className="dshDesktopOverlay" data-shell-overlay>{renderSlot('shell.overlay', {})}</div>
      {!collapsed && <ResizeHandle side="sidebar" left={columns.sidebar} size={columns.sidebar} onResize={(width) => layout.setSidebar(width)} />}
      {columns.details > 0 && <ResizeHandle side="details" left={viewport - columns.details} size={columns.details} onResize={(width) => layout.setDetails(width)} />}
    </div>
  )
}

function AgentHomeEntry(props: { wide: boolean; active: boolean; onOpen(): void }) {
  return (
    <button
      type="button"
      className={`dshDesktopFooterAction${props.wide ? '' : ' is-rail'}${props.active ? ' is-active' : ''}`}
      aria-label="Agent"
      aria-pressed={props.active}
      title={props.wide ? undefined : 'Agent'}
      onClick={props.onOpen}
    >
      <svg aria-hidden="true" viewBox="0 0 24 24" fill="none">
        <path d="m12 3 2.4 5.2L20 9.3l-4 3.9 1 5.8-5-2.9-5 2.9 1-5.8-4-3.9 5.6-1.1L12 3Z" />
      </svg>
      {props.wide && <span className="dshDesktopFooterActionLabel">Agent</span>}
    </button>
  )
}

function ResizeHandle(props: { side: 'sidebar' | 'details'; left: number; size: number; onResize(width: number): void }) {
  const origin = useRef(0)
  const base = useRef(0)
  const onPointerDown = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    origin.current = event.clientX
    base.current = props.size
    event.currentTarget.setPointerCapture(event.pointerId)
  }, [props.size])
  const onPointerMove = useCallback((event: React.PointerEvent<HTMLDivElement>) => {
    if (!event.currentTarget.hasPointerCapture(event.pointerId)) return
    const delta = event.clientX - origin.current
    props.onResize(base.current + (props.side === 'sidebar' ? delta : -delta))
  }, [props])
  return <div className="dshDesktopResizeHandle" data-side={props.side} style={{ left: props.left }} onPointerDown={onPointerDown} onPointerMove={onPointerMove} />
}
