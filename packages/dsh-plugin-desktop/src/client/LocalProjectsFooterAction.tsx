import { useSyncExternalStore } from 'react'
import type { LocalProjectsState } from './local-projects-state'

export interface LocalProjectsFooterActionProps {
  wide: boolean
  state: LocalProjectsState
}

export function LocalProjectsFooterAction({ wide, state }: LocalProjectsFooterActionProps) {
  const opened = useSyncExternalStore(state.subscribe, state.getSnapshot)
  return (
    <button
      type="button"
      className={`dshDesktopFooterAction${wide ? '' : ' is-rail'}${opened ? ' is-active' : ''}`}
      aria-label="本地项目"
      aria-pressed={opened}
      title={wide ? undefined : '本地项目'}
      onClick={() => state.toggle()}
    >
      <LocalProjectsIcon />
      {wide && <span className="dshDesktopFooterActionLabel">本地项目</span>}
    </button>
  )
}

function LocalProjectsIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none">
      <path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z" />
      <path d="m4 7.5 8 4.5 8-4.5M12 12v9" />
    </svg>
  )
}
