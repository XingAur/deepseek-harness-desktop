import { useEffect, useRef, useState } from 'react'
import type { ProjectCoverToken } from './project-model'
import { ProjectCoverPicker } from './ProjectCoverPicker'

const ITEM_COUNT = 6

export interface ProjectContextMenuProps {
  x: number
  y: number
  cover: ProjectCoverToken
  pinned: boolean
  disabled?: boolean
  running?: boolean
  onOpenSession?(): void
  onStopApp?(): void
  onRename(): void
  onCoverChange(cover: ProjectCoverToken): Promise<void>
  onPinChange(pinned: boolean): Promise<void>
  onDelete(): void
  onClose(): void
}

export function ProjectContextMenu(props: ProjectContextMenuProps) {
  const { x, y, cover, pinned, disabled = false, running = false, onOpenSession, onStopApp, onRename, onCoverChange, onPinChange, onDelete, onClose } = props
  const menuRef = useRef<HTMLDivElement>(null)
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([])
  const [activeIndex, setActiveIndex] = useState(0)
  const [view, setView] = useState<'actions' | 'covers'>('actions')

  useEffect(() => { if (view === 'actions') itemRefs.current[activeIndex]?.focus() }, [activeIndex, view])
  useEffect(() => {
    const closeFromOutside = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) onClose()
    }
    document.addEventListener('pointerdown', closeFromOutside)
    return () => document.removeEventListener('pointerdown', closeFromOutside)
  }, [onClose])

  const run = (action: () => void | Promise<void>) => { void Promise.resolve(action()).finally(onClose) }

  return (
    <div
      ref={menuRef}
      className="dshDesktopProjectContextMenu"
      role="menu"
      aria-label="项目操作"
      style={{
        left: Math.max(8, Math.min(x, window.innerWidth - 204)),
        top: Math.max(8, Math.min(y, window.innerHeight - 260)),
      }}
      onKeyDown={(event) => {
        if (event.key === 'Escape') {
          event.preventDefault()
          if (view === 'covers') setView('actions'); else onClose()
          return
        }
        if (view !== 'actions') return
        if (event.key === 'ArrowDown') { event.preventDefault(); setActiveIndex((activeIndex + 1) % ITEM_COUNT) }
        else if (event.key === 'ArrowUp') { event.preventDefault(); setActiveIndex((activeIndex - 1 + ITEM_COUNT) % ITEM_COUNT) }
        else if (event.key === 'Home') { event.preventDefault(); setActiveIndex(0) }
        else if (event.key === 'End') { event.preventDefault(); setActiveIndex(ITEM_COUNT - 1) }
        else if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault(); itemRefs.current[activeIndex]?.click()
        }
      }}
    >
      {view === 'covers' ? (
        <ProjectCoverPicker current={cover} onBack={() => setView('actions')} onSelect={(nextCover) => run(() => onCoverChange(nextCover))} />
      ) : (
        <>
          <button ref={(node) => { itemRefs.current[0] = node }} type="button" role="menuitem" disabled={disabled} onClick={() => run(() => onOpenSession?.())}>打开会话继续开发</button>
          <button ref={(node) => { itemRefs.current[1] = node }} type="button" role="menuitem" disabled={disabled || !running} onClick={() => run(() => onStopApp?.())}>停止应用</button>
          <button ref={(node) => { itemRefs.current[2] = node }} type="button" role="menuitem" disabled={disabled} onClick={() => run(onRename)}>修改名称</button>
          <button ref={(node) => { itemRefs.current[3] = node }} type="button" role="menuitem" disabled={disabled} onClick={() => setView('covers')}>修改封面 <span aria-hidden="true">›</span></button>
          <button ref={(node) => { itemRefs.current[4] = node }} type="button" role="menuitem" disabled={disabled} onClick={() => run(() => onPinChange(!pinned))}>{pinned ? '取消置顶' : '置顶'}</button>
          <div className="dshDesktopProjectMenuDivider" />
          <button ref={(node) => { itemRefs.current[5] = node }} type="button" role="menuitem" className="dshDesktopProjectMenuDanger" disabled={disabled} onClick={() => run(onDelete)}>删除项目</button>
        </>
      )}
    </div>
  )
}
