import { describe, expect, it } from 'vitest'
import { initialDesktopColorScheme, persistDesktopColorScheme, themeFromWorkbenchMessage } from './theme-message'

describe('themeFromWorkbenchMessage', () => {
  const frameWindow = window

  function message(data: unknown, source: Window | null = frameWindow) {
    return { data, source } as MessageEvent
  }

  it('accepts a supported theme from the active workbench frame', () => {
    expect(themeFromWorkbenchMessage(
      message({ type: 'dsh-desktop-theme', colorScheme: 'light' }),
      frameWindow,
    )).toBe('light')
  })

  it.each([
    [{ type: 'other', colorScheme: 'dark' }],
    [{ type: 'dsh-desktop-theme', colorScheme: 'system' }],
    [null],
    ['dark'],
  ])('rejects malformed message data %#', (data) => {
    expect(themeFromWorkbenchMessage(message(data), frameWindow)).toBeNull()
  })

  it('rejects messages from another window', () => {
    const otherWindow = { postMessage() {} } as unknown as Window
    expect(themeFromWorkbenchMessage(
      message({ type: 'dsh-desktop-theme', colorScheme: 'dark' }, otherWindow),
      frameWindow,
    )).toBeNull()
  })

  it('uses light for a first installation and persists a workbench selection', () => {
    const values = new Map<string, string>()
    const storage = {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => values.set(key, value),
    }
    expect(initialDesktopColorScheme(storage)).toBe('light')
    persistDesktopColorScheme('dark', storage)
    expect(initialDesktopColorScheme(storage)).toBe('dark')
  })
})
