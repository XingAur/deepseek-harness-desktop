import { useSyncExternalStore } from 'react'
import type { ExtensionCenterState } from './extension-center-state'

export interface ExtensionCenterFooterActionProps {
  wide: boolean
  state: ExtensionCenterState
}

export function ExtensionCenterFooterAction({ wide, state }: ExtensionCenterFooterActionProps) {
  const opened = useSyncExternalStore(state.subscribe, state.getSnapshot)
  return (
    <button
      type="button"
      className={`dshDesktopFooterAction${wide ? '' : ' is-rail'}${opened ? ' is-active' : ''}`}
      aria-label="扩展中心"
      aria-pressed={opened}
      title={wide ? undefined : '扩展中心'}
      onClick={() => state.toggle()}
    >
      <ExtensionCenterIcon />
      {wide && <span className="dshDesktopFooterActionLabel">扩展中心</span>}
    </button>
  )
}

function ExtensionCenterIcon() {
  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none">
      <path d="M10 4h4v4h4v4h-4v4h-4v-4H6V8h4V4Z" />
      <path d="m16.5 15.5 3 3-3 3-3-3 3-3Z" />
    </svg>
  )
}
