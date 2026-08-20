import { describe, expect, it } from 'vitest'
import { readIcoSizes, verifyWindowsIcon } from './verify-windows-icon.mjs'

describe('Windows application icon', () => {
  it('uses one multi-size icon for the app installer and uninstaller', () => {
    expect(verifyWindowsIcon()).toEqual(expect.arrayContaining([16, 24, 32, 48, 64, 256]))
    expect(readIcoSizes('src-tauri/icons/icon.ico')).toEqual([16, 24, 32, 48, 64, 256])
  })
})
