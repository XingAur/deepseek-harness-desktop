import { useSyncExternalStore } from 'react'

/**
 * 左侧「插件」入口的开关状态，与 LocalProjectsState 同款模式：
 * 开关式全页面，进入时独占会话区。
 */
export class PluginCenterState {
  private opened = false
  private readonly listeners = new Set<() => void>()

  readonly getSnapshot = () => this.opened

  readonly subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  open(): void { this.setOpened(true) }
  close(): void { this.setOpened(false) }
  toggle(): void { this.setOpened(!this.opened) }

  private setOpened(opened: boolean): void {
    if (this.opened === opened) return
    this.opened = opened
    this.listeners.forEach((listener) => listener())
  }
}

export function usePluginCenterOpen(state: PluginCenterState): boolean {
  return useSyncExternalStore(state.subscribe, state.getSnapshot)
}
