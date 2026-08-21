import { useRef, useState } from 'react'
import { ProjectContextMenu } from './ProjectContextMenu'
import type { ProjectCardModel, ProjectCoverToken } from './project-model'

export interface ProjectCardProps {
  card: ProjectCardModel
  selected: boolean
  unavailable: boolean
  recent?: boolean
  disabled?: boolean
  launchable?: boolean
  running?: boolean
  onSelect(): void
  onOpen(): Promise<void>
  onOpenSession(): void
  onStopApp(): Promise<void> | void
  onRename(title: string): Promise<void>
  onCoverChange(cover: ProjectCoverToken): Promise<void>
  onPinChange(pinned: boolean): Promise<void>
  onDelete(): void
}

export function ProjectCard(props: ProjectCardProps) {
  const { card, selected, unavailable, recent = false, disabled = false, launchable = false, running = false, onSelect, onOpen, onOpenSession, onStopApp, onRename, onCoverChange, onPinChange, onDelete } = props
  const surfaceRef = useRef<HTMLDivElement>(null)
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null)
  const [editing, setEditing] = useState(false)
  const [title, setTitle] = useState(card.title)
  const [renameError, setRenameError] = useState<string | null>(null)

  const closeMenu = () => {
    setMenu(null)
    surfaceRef.current?.focus()
  }

  const startRename = () => {
    setMenu(null)
    setTitle(card.title)
    setRenameError(null)
    setEditing(true)
  }

  const submitRename = async () => {
    const normalized = title.trim()
    const length = Array.from(normalized).length
    if (length === 0 || length > 80) {
      setRenameError(length === 0 ? '项目名称不能为空' : '项目名称不能超过 80 个字符')
      return
    }
    await onRename(normalized)
    setEditing(false)
    setRenameError(null)
    queueMicrotask(() => surfaceRef.current?.focus())
  }

  return (
    <article
      className="dshDesktopProjectCard"
      data-project-id={card.id}
      data-cover={card.cover}
      data-selected={selected || undefined}
      data-unavailable={unavailable || undefined}
      data-recent={recent || undefined}
      data-pinned={card.pinned || undefined}
    >
      <div
        ref={surfaceRef}
        className="dshDesktopProjectCardSurface"
        role="button"
        tabIndex={disabled ? -1 : 0}
        aria-label={`项目 ${card.title}`}
        aria-selected={selected}
        aria-disabled={disabled || undefined}
        onClick={() => { if (!disabled && !editing) onSelect() }}
        onDoubleClick={() => { if (!disabled && !unavailable && !editing) void onOpen() }}
        onContextMenu={(event) => {
          if (disabled) return
          event.preventDefault()
          onSelect()
          setMenu({ x: event.clientX, y: event.clientY })
        }}
        onKeyDown={(event) => {
          if (disabled || editing) return
          if (event.key === 'Enter') {
            event.preventDefault()
            if (!unavailable) void onOpen()
          } else if (event.key === 'F2') {
            event.preventDefault()
            startRename()
          } else if (event.key === 'Delete') {
            event.preventDefault()
            onDelete()
          } else if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
            event.preventDefault()
            setMenu({ x: 24, y: 24 })
          }
        }}
      >
        <div className="dshDesktopProjectCover" aria-hidden="true"><span>⌘</span></div>
        <div className="dshDesktopProjectCardBody">
          {editing ? (
            <input
              autoFocus
              aria-label="项目名称"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              onClick={(event) => event.stopPropagation()}
              onDoubleClick={(event) => event.stopPropagation()}
              onKeyDown={(event) => {
                event.stopPropagation()
                if (event.key === 'Enter') void submitRename()
                else if (event.key === 'Escape') {
                  setEditing(false)
                  setRenameError(null)
                  queueMicrotask(() => surfaceRef.current?.focus())
                }
              }}
            />
          ) : <h2>{card.title}</h2>}
          <p title={card.path}>{card.path}</p>
          <div className="dshDesktopProjectMeta">
            <span>{card.sessionIds.length > 0 ? `${card.sessionIds.length} 个会话` : '尚无会话'}</span>
            {card.pinned && <span>已置顶</span>}
            {running && <span className="dshDesktopProjectBadge" data-kind="running">运行中</span>}
            {!running && launchable && <span className="dshDesktopProjectBadge" data-kind="launchable">可运行</span>}
            {unavailable && <strong>路径不可用</strong>}
          </div>
          {renameError !== null && <small className="dshDesktopProjectRenameError" role="alert">{renameError}</small>}
        </div>
      </div>
      {menu !== null && (
        <ProjectContextMenu
          x={menu.x}
          y={menu.y}
          cover={card.cover}
          pinned={card.pinned}
          disabled={disabled}
          running={running}
          onOpenSession={onOpenSession}
          onStopApp={onStopApp}
          onRename={startRename}
          onCoverChange={onCoverChange}
          onPinChange={onPinChange}
          onDelete={onDelete}
          onClose={closeMenu}
        />
      )}
    </article>
  )
}
