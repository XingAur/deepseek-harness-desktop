import type { DesktopPlatform } from './contracts'

export interface DesktopEnvironment {
  mode: 'advanced'
  platform: DesktopPlatform
  parentOrigin?: string
}

export function parseDesktopEnvironment(search: string): DesktopEnvironment | null {
  const params = new URLSearchParams(search)
  if (params.get('dsh-desktop-mode') !== 'advanced') return null
  const platform = params.get('dsh-desktop-platform')
  if (platform !== 'win32' && platform !== 'darwin') return null
  return {
    mode: 'advanced',
    platform,
    parentOrigin: parseParentOrigin(params.get('dsh-desktop-parent-origin')),
  }
}

function parseParentOrigin(value: string | null): string | undefined {
  if (value === null || value === '') return undefined
  try {
    const url = new URL(value)
    if (!['http:', 'https:', 'tauri:'].includes(url.protocol)) return undefined
    if (url.username !== '' || url.password !== '' || (url.pathname !== '' && url.pathname !== '/')) return undefined
    if (url.search !== '' || url.hash !== '' || url.hostname === '') return undefined
    return `${url.protocol}//${url.host}`
  } catch {
    return undefined
  }
}
