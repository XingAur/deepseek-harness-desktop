import { useRef, type MouseEvent } from 'react'
import type { WindowControls } from './window-client'

export interface TitleBarProps {
  controls: WindowControls
}

export function TitleBar({ controls }: TitleBarProps) {
  const dragArmed = useRef(false)
  const dragStarted = useRef(false)

  const resetDrag = () => {
    dragArmed.current = false
    dragStarted.current = false
  }

  const handleMouseDown = (event: MouseEvent<HTMLElement>) => {
    if (event.buttons !== 1 || (event.target as HTMLElement).closest('button')) {
      resetDrag()
      return
    }
    if (event.detail === 2) {
      resetDrag()
      void controls.toggleMaximize()
      return
    }
    dragArmed.current = true
    dragStarted.current = false
  }

  const handleMouseMove = (event: MouseEvent<HTMLElement>) => {
    if (!dragArmed.current || dragStarted.current || event.buttons !== 1) return
    dragStarted.current = true
    void controls.startDragging()
  }

  return (
    <header
      className="titleBar"
      onMouseDown={handleMouseDown}
      onMouseMove={handleMouseMove}
      onMouseUp={resetDrag}
      onMouseLeave={resetDrag}
    >
      <div className="windowControls">
        <TrafficButton label="关闭窗口" className="trafficClose" onClick={() => void controls.hide()}>
          <path d="M5 5l10 10M15 5L5 15" />
        </TrafficButton>
        <TrafficButton label="最小化窗口" className="trafficMinimize" onClick={() => void controls.minimize()}>
          <path d="M5 10h10" />
        </TrafficButton>
        <TrafficButton label="最大化或还原窗口" className="trafficMaximize" onClick={() => void controls.toggleMaximize()}>
          <path d="M6 14l8-8M8 6h6v6" />
        </TrafficButton>
      </div>
    </header>
  )
}

function TrafficButton(props: {
  label: string
  className: string
  onClick(): void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      className={`trafficButton ${props.className}`}
      aria-label={props.label}
      title={props.label}
      onClick={props.onClick}
    >
      <svg viewBox="0 0 20 20" aria-hidden="true">{props.children}</svg>
    </button>
  )
}
