import type { DesktopPlatform } from './contracts'

export interface DesktopEnvironment {
  mode: 'advanced'
  platform: DesktopPlatform
}

export function parseDesktopEnvironment(search: string): DesktopEnvironment | null {
  const params = new URLSearchParams(search)
  if (params.get('dsh-desktop-mode') !== 'advanced') return null
  const platform = params.get('dsh-desktop-platform')
  if (platform !== 'win32' && platform !== 'darwin') return null
  return { mode: 'advanced', platform }
}
