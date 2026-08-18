export class DesktopThemePresenter {
  apply(snapshot: unknown) {
    if (typeof snapshot !== 'object' || snapshot === null) return
    const values = snapshot as Record<string, unknown>
    const root = document.documentElement.style
    const background = typeof values.background === 'string' ? values.background : '#151517'
    const foreground = typeof values.foreground === 'string' ? values.foreground : '#f4f4f5'
    root.setProperty('--dsh-desktop-background', background)
    root.setProperty('--dsh-desktop-foreground', foreground)
  }
  dispose() {
    document.documentElement.style.removeProperty('--dsh-desktop-background')
    document.documentElement.style.removeProperty('--dsh-desktop-foreground')
  }
}
