import { describe, expect, it } from 'vitest'
import { parseDesktopEnvironment } from '../src/client/environment'

describe('desktop client environment', () => {
  it('accepts only manager-owned advanced markers', () => {
    expect(parseDesktopEnvironment('?dsh-desktop-mode=advanced&dsh-desktop-platform=darwin')).toEqual({ mode: 'advanced', platform: 'darwin' })
    expect(parseDesktopEnvironment('?dsh-desktop-mode=advanced&dsh-desktop-platform=linux')).toBeNull()
    expect(parseDesktopEnvironment('?dsh-desktop-mode=compatibility&dsh-desktop-platform=win32')).toBeNull()
  })
})
