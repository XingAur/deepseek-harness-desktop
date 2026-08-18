import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import type { AdvancedFrameProps } from './contracts'
import {
  computeDesktopColumns, MACOS_SIDEBAR_COLLAPSED, SIDEBAR_AUTO_COLLAPSE,
  SIDEBAR_COLLAPSED, SIDEBAR_DEFAULT,
} from './layout-state'
import { MarketPage } from './MarketPage'

export function AdvancedFrame({ layout, platform, renderSlot, useSessions }: AdvancedFrameProps) {
  const subscribe = useCallback((listener: () => void) => layout.subscribe(listener), [layout])
  const panels = useSyncExternalStore(subscribe, layout.getSnapshot)
  const frameRef = useRef<HTMLDivElement>(null)
  const [viewport, setViewport] = useState(() => window.innerWidth)
  const [marketOpen, setMarketOpen] = useState(false)
  const detailsSession = useSessions((state) => {
    const current = state.current
    return current !== undefined && state.byId[current]?.blank === false ? current : undefined
  })

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
  useEffect(() => { if (marketOpen) layout.closeDetails() }, [layout, marketOpen])

  const collapsed = panels.narrow ? !panels.narrowExpanded : panels.sidebar === 0
  const sidebarPreference = collapsed ? 0 : panels.sidebar === 0 ? SIDEBAR_DEFAULT : panels.sidebar
  const columns = computeDesktopColumns(
    viewport,
    sidebarPreference,
    marketOpen || detailsSession === undefined ? 0 : panels.details,
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
      {platform === 'darwin' && <div className="dshDesktopMacCaptionRow" aria-hidden="true" />}
      {platform === 'win32' && <div className="dshDesktopWindowsCaptionRow" aria-hidden="true" />}
      <aside className="dshDesktopSidebarSurface">
        <div className="dshDesktopUpstreamSidebar">{renderSlot('sidebar', { collapsed, width: columns.sidebar })}</div>
        {!collapsed && (
          <button className="dshDesktopMarketEntry" data-active={marketOpen} onClick={() => setMarketOpen((value) => !value)}>
            ◇&nbsp;&nbsp;社区插件
          </button>
        )}
      </aside>
      <main className="dshDesktopConversationSurface">
        {marketOpen ? <MarketPage onClose={() => setMarketOpen(false)} /> : renderSlot('conversation', {})}
      </main>
      <aside className="dshDesktopDetailsSurface">{renderSlot('details', {})}</aside>
      <div className="dshDesktopOverlay" data-shell-overlay>{renderSlot('shell.overlay', {})}</div>
      {!collapsed && <ResizeHandle side="sidebar" left={columns.sidebar} size={columns.sidebar} onResize={(width) => layout.setSidebar(width)} />}
      {columns.details > 0 && <ResizeHandle side="details" left={viewport - columns.details} size={columns.details} onResize={(width) => layout.setDetails(width)} />}
    </div>
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
