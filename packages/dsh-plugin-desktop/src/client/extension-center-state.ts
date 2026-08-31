export class ExtensionCenterState {
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
