export const SIDEBAR_MIN = 220
export const SIDEBAR_DEFAULT = 320
export const SIDEBAR_MAX = 480
export const DETAILS_MIN = 280
export const DETAILS_DEFAULT = 380
export const DETAILS_MAX = 620
export const SIDEBAR_COLLAPSED = 72
export const MACOS_SIDEBAR_COLLAPSED = 84
export const SIDEBAR_AUTO_COLLAPSE = 980

export interface DesktopLayoutSnapshot {
  sidebar: number
  details: number
  narrow: boolean
  narrowExpanded: boolean
}

const clamp = (value: number, minimum: number, maximum: number) => Math.min(maximum, Math.max(minimum, value))

export function computeDesktopColumns(viewport: number, sidebar: number, details: number, collapsedWidth: number) {
  const safeViewport = Math.max(0, viewport)
  const safeSidebar = sidebar === 0 ? collapsedWidth : clamp(sidebar, SIDEBAR_MIN, SIDEBAR_MAX)
  const remainingAfterSidebar = Math.max(0, safeViewport - safeSidebar)
  const maxDetails = Math.max(0, Math.min(DETAILS_MAX, remainingAfterSidebar - 420))
  const safeDetails = details === 0 || maxDetails < DETAILS_MIN ? 0 : clamp(details, DETAILS_MIN, maxDetails)
  return { sidebar: safeSidebar, details: safeDetails }
}

export class DesktopLayoutState {
  private snapshot: DesktopLayoutSnapshot = { sidebar: SIDEBAR_DEFAULT, details: DETAILS_DEFAULT, narrow: false, narrowExpanded: false }
  private readonly listeners = new Set<() => void>()

  subscribe(listener: () => void) { this.listeners.add(listener); return () => this.listeners.delete(listener) }
  getSnapshot = () => this.snapshot
  setSidebar(sidebar: number) { this.update({ sidebar: sidebar === 0 ? 0 : clamp(sidebar, SIDEBAR_MIN, SIDEBAR_MAX) }) }
  toggleSidebar() {
    if (this.snapshot.narrow) this.toggleNarrow()
    else this.setSidebar(this.snapshot.sidebar === 0 ? SIDEBAR_DEFAULT : 0)
  }
  setDetails(details: number) { this.update({ details: details === 0 ? 0 : clamp(details, DETAILS_MIN, DETAILS_MAX) }) }
  openDetails() { if (this.snapshot.details === 0) this.setDetails(DETAILS_DEFAULT) }
  closeDetails() { this.setDetails(0) }
  setNarrow(narrow: boolean) { this.update({ narrow, narrowExpanded: narrow ? this.snapshot.narrowExpanded : false }) }
  toggleNarrow() { this.update({ narrowExpanded: !this.snapshot.narrowExpanded }) }

  private update(patch: Partial<DesktopLayoutSnapshot>) {
    const next = { ...this.snapshot, ...patch }
    if (JSON.stringify(next) === JSON.stringify(this.snapshot)) return
    this.snapshot = next
    for (const listener of this.listeners) listener()
  }
}
