export type DesktopColorScheme = 'light' | 'dark'

const storedThemeKey = 'dsh-desktop-color-scheme'

// 首次安装时官方工作台以浅色启动；在它的首条主题消息抵达前，壳层也使用浅色，
// 避免系统深色偏好造成启动页与工作台不一致。
export function initialDesktopColorScheme(storage: Pick<Storage, 'getItem'> | undefined = globalThis.localStorage): DesktopColorScheme {
  try {
    const stored = storage?.getItem(storedThemeKey)
    return stored === 'dark' ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

export function persistDesktopColorScheme(
  colorScheme: DesktopColorScheme,
  storage: Pick<Storage, 'setItem'> | undefined = globalThis.localStorage,
): void {
  try {
    storage?.setItem(storedThemeKey, colorScheme)
  } catch {
    // 隐私模式或受限 WebView 不应阻断主题同步。
  }
}

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
