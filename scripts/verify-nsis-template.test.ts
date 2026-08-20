import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { verifyNsisTemplate } from './verify-nsis-template.mjs'

describe('custom NSIS template', () => {
  it('installs only the desktop shell', () => {
    const source = readFileSync('src-tauri/windows/installer.nsi', 'utf8')
    expect(source).toContain('Section Install')
    expect(source).toContain('File "${MAINBINARYSRCPATH}"')
    expect(source).not.toContain('Section ProvisionRuntime')
    expect(source).not.toContain('--provision-runtime')
    expect(source).not.toContain('CommitProvisioning')
    expect(source).not.toContain('RollbackProvisioning')
  })

  it('copies the shell before registry and shortcuts', () => {
    const source = readFileSync('src-tauri/windows/installer.nsi', 'utf8')
    const copy = source.indexOf('File "${MAINBINARYSRCPATH}"')
    const uninstaller = source.indexOf('WriteUninstaller')
    const registry = source.indexOf('WriteRegStr SHCTX "${UNINSTKEY}" "DisplayName"')
    const shortcut = source.lastIndexOf('Call CreateOrUpdateStartMenuShortcut')
    expect(copy).toBeGreaterThan(0)
    expect(copy).toBeLessThan(uninstaller)
    expect(uninstaller).toBeLessThan(registry)
    expect(registry).toBeLessThan(shortcut)
  })

  it('preserves data by default and delegates explicit cleanup before deleting the app binary', () => {
    const source = readFileSync('src-tauri/windows/installer.nsi', 'utf8')
    const cleanup = source.indexOf('--cleanup-app-data')
    const binaryDelete = source.indexOf('Delete "$INSTDIR\\${MAINBINARYNAME}.exe"')
    const legacyRecursiveDelete = source.indexOf('RmDir /r "$LOCALAPPDATA\\${BUNDLEID}"')
    expect(source).toContain('StrCpy $DeleteAppDataCheckboxState 0')
    expect(cleanup).toBeGreaterThan(0)
    expect(cleanup).toBeLessThan(binaryDelete)
    expect(source.indexOf('StrCpy $DeleteAppDataCheckboxState 0', cleanup)).toBeLessThan(
      legacyRecursiveDelete,
    )
  })

  it('pins the upstream Tauri template baseline', () => {
    const metadata = verifyNsisTemplate()
    expect(metadata.tauriCliVersion).toBe('2.11.4')
    expect(metadata.tauriBundlerVersion).toBe('2.9.4')
    expect(metadata.upstreamSha256).toMatch(/^[a-f0-9]{64}$/)
    expect(metadata.requiredMarkers).toEqual(expect.arrayContaining([
      'MAINBINARYSRCPATH',
      'Section Install',
      'WriteUninstaller',
    ]))
  })
})
