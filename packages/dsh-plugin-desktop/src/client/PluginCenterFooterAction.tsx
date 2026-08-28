import { useSyncExternalStore } from 'react'
import type { PluginCenterState } from './plugin-center-state'

export interface PluginCenterFooterActionProps {
  wide: boolean
  state: PluginCenterState
}

export function PluginCenterFooterAction({ wide, state }: PluginCenterFooterActionProps) {
  const opened = useSyncExternalStore(state.subscribe, state.getSnapshot)
  return (
    <button
      type="button"
      className={`dshDesktopFooterAction${wide ? '' : ' is-rail'}${opened ? ' is-active' : ''}`}
      aria-label="插件"
      aria-pressed={opened}
      title={wide ? undefined : '插件'}
      onClick={() => state.toggle()}
    >
      <svg aria-hidden="true" viewBox="0 0 24 24" fill="none">
        <path d="M10 3.5 4.5 9v6L10 20.5M14 3.5 19.5 9v6L14 20.5M9.5 12h5" />
      </svg>
      {wide && <span className="dshDesktopFooterActionLabel">插件</span>}
    </button>
  )
}
