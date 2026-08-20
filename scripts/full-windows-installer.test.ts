import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'

import { createFullTauriConfig } from './full-windows-installer.mjs'

describe('full Windows installer contract', () => {
  it('keeps the online installer free of Runtime resources and install hooks', () => {
    const online = JSON.parse(readFileSync('src-tauri/tauri.windows.conf.json', 'utf8'))
    expect(online.bundle.resources).toBeUndefined()
    expect(online.bundle.windows.nsis.installerHooks).toBeUndefined()
  })

  it('maps only the signed Windows Runtime files into the fixed resource paths', () => {
    const config = createFullTauriConfig('E:/repo')
    expect(config.bundle.resources).toEqual({
      'E:/repo/runtime-build/windows-x86_64/dsh-runtime-windows-x86_64.zip':
        'runtime/dsh-runtime-windows-x86_64.zip',
      'E:/repo/runtime-build/windows-x86_64/runtime-windows-x86_64.json':
        'runtime/manifests/runtime-windows-x86_64.json',
    })
    expect(config.bundle.windows.nsis.installerHooks)
      .toBe('E:/repo/src-tauri/windows/full-installer-hooks.nsh')
  })

  it('invokes only the fixed internal mode after install', () => {
    const hook = readFileSync('src-tauri/windows/full-installer-hooks.nsh', 'utf8')
    expect(hook).toContain('!macro NSIS_HOOK_POSTINSTALL')
    expect(hook).toContain('--install-bundled-runtime')
    expect(hook).toContain('ExecWait')
    expect(hook).not.toContain('https://')
    expect(hook).not.toContain('runtime-build')
  })
})
