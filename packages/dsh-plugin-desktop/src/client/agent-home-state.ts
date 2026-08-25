import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'

/**
 * Sidebar entry that opens the Agent console home page. Shares the open/close
 * pattern with LocalProjectsState so the advanced frame can render a
 * full-surface page instead of a settings section.
 */
export class AgentHomeState {
  private opened = false
  private readonly listeners = new Set<() => void>()

  readonly getSnapshot = () => this.opened

  readonly subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  open(): void {
    this.setOpened(true)
  }

  close(): void {
    this.setOpened(false)
  }

  toggle(): void {
    this.setOpened(!this.opened)
  }

  private setOpened(opened: boolean): void {
    if (this.opened === opened) return
    this.opened = opened
    this.listeners.forEach((listener) => listener())
  }
}

export function useAgentHomeOpen(state: AgentHomeState): boolean {
  return useSyncExternalStore(state.subscribe, state.getSnapshot)
}

export function useAgentHome(state: AgentHomeState) {
  const [providerId, setProviderId] = useState<string | null>(null)
  const openHome = useCallback(() => { state.open(); setProviderId(null) }, [state])
  const openWorkbench = useCallback((next: string) => { state.open(); setProviderId(next) }, [state])
  return { providerId, openHome, openWorkbench }
}

/** Keep the Agent page state aligned with the advanced frame lifecycle. */
export function useAgentHomeEffects(state: AgentHomeState, projectsOpen: boolean, onClose: () => void): void {
  const opened = useAgentHomeOpen(state)
  useEffect(() => {
    if (opened && projectsOpen) onClose()
  }, [opened, projectsOpen, onClose])
}
