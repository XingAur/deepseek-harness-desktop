export type DesktopColorScheme = 'light' | 'dark'

export function themeFromWorkbenchMessage(
  event: MessageEvent,
  frameWindow: Window | null,
): DesktopColorScheme | null {
  if (frameWindow === null || event.source !== frameWindow) return null
  if (typeof event.data !== 'object' || event.data === null || Array.isArray(event.data)) return null

  const message = event.data as Record<string, unknown>
  if (message.type !== 'dsh-desktop-theme') return null
  return message.colorScheme === 'light' || message.colorScheme === 'dark'
    ? message.colorScheme
    : null
}
