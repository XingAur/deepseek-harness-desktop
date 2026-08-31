import { describe, expect, it } from 'vitest'
import { inject } from '../src/client/index'
import { parseDesktopEnvironment } from '../src/client/environment'

describe('desktop client environment', () => {
  it('declares only browser-client services available during boot', () => {
    expect(inject).toEqual(['slots', 'sessions', 'theme', 'workspaces'])
  })

  it('accepts only manager-owned advanced markers', () => {
    expect(parseDesktopEnvironment('?dsh-desktop-mode=advanced&dsh-desktop-platform=darwin')).toEqual({ mode: 'advanced', platform: 'darwin' })
    expect(parseDesktopEnvironment('?dsh-desktop-mode=advanced&dsh-desktop-platform=linux')).toBeNull()
    expect(parseDesktopEnvironment('?dsh-desktop-mode=compatibility&dsh-desktop-platform=win32')).toBeNull()
  })

  it('reads the explicitly supplied desktop parent origin', () => {
    expect(parseDesktopEnvironment('?dsh-desktop-mode=advanced&dsh-desktop-platform=darwin&dsh-desktop-parent-origin=tauri%3A%2F%2Flocalhost')).toEqual({
      mode: 'advanced',
      platform: 'darwin',
      parentOrigin: 'tauri://localhost',
    })
  })
})
