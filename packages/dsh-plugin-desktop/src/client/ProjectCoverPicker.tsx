import { useEffect, useRef, useState } from 'react'
import { PROJECT_COVERS, type ProjectCoverToken } from './project-model'

const COVER_LABELS: Record<ProjectCoverToken, string> = {
  'aurora-blue': '极光蓝', sunset: '日落', forest: '森林', graphite: '石墨', violet: '紫罗兰',
}

export interface ProjectCoverPickerProps {
  current: ProjectCoverToken
  onSelect(cover: ProjectCoverToken): void
  onBack(): void
}

export function ProjectCoverPicker({ current, onSelect, onBack }: ProjectCoverPickerProps) {
  const [activeIndex, setActiveIndex] = useState(Math.max(0, PROJECT_COVERS.indexOf(current)))
  const refs = useRef<Array<HTMLButtonElement | null>>([])

  useEffect(() => { refs.current[activeIndex]?.focus() }, [activeIndex])

  return (
    <div className="dshDesktopProjectCoverPicker">
      <button type="button" className="dshDesktopProjectMenuBack" onClick={onBack}>← 返回</button>
      <div className="dshDesktopProjectCoverGrid" role="group" aria-label="内置封面">
        {PROJECT_COVERS.map((cover, index) => (
          <button
            key={cover}
            ref={(node) => { refs.current[index] = node }}
            type="button"
            role="menuitemradio"
            aria-checked={cover === current}
            aria-label={COVER_LABELS[cover]}
            data-cover={cover}
            onClick={() => onSelect(cover)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
                event.preventDefault(); setActiveIndex((index + 1) % PROJECT_COVERS.length)
              } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
                event.preventDefault(); setActiveIndex((index - 1 + PROJECT_COVERS.length) % PROJECT_COVERS.length)
              }
            }}
          >
            <span aria-hidden="true" />
            <small>{COVER_LABELS[cover]}</small>
          </button>
        ))}
      </div>
    </div>
  )
}
