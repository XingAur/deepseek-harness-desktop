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

  it('offers explicit dependent app-data and project cleanup choices', () => {
    const source = readFileSync('src-tauri/windows/installer.nsi', 'utf8')
    const projectCleanup = source.indexOf('--cleanup-projects')
    const appDataCleanup = source.indexOf('--cleanup-app-data')
    const binaryDelete = source.indexOf('Delete "$INSTDIR\\${MAINBINARYNAME}.exe"')

    expect(source).toContain('Var DeleteProjectsCheckbox')
    expect(source).toContain('StrCpy $DeleteProjectsCheckboxState 0')
    expect(source).toContain('/DELETEPROJECTS')
    expect(source).toContain('--list-uninstall-projects')
    expect(source).toContain('FileReadUTF16LE')
    expect(source).not.toMatch(/^\s*FileRead\s/m)
    expect(source).toContain('无法读取项目清单')
    expect(source).toContain('没有可删除的本地项目')
    expect(source).toContain('${EM_SETSEL}')
    expect(source).toContain('${EM_REPLACESEL}')
    expect(source).not.toContain('StrCpy $ProjectPreviewText "$ProjectPreviewText')
    expect(source).toContain('MB_ICONSTOP|MB_YESNO')
    expect(source).toContain('Function un.DeleteProjectsChanged')
    expect(source).toContain('Function un.DeleteAppDataChanged')
    expect(source).toContain('StrCpy $DeleteAppDataCheckboxState 1')
    expect(source).toContain('$TEMP\\deepseek-harness-uninstall-projects-$UninstallToken.txt')
    expect(source).toContain('$TEMP\\deepseek-harness-uninstall-report-$UninstallToken.txt')
    expect(source).toContain('Function un.onGUIEnd')
    expect(projectCleanup).toBeGreaterThan(0)
    expect(projectCleanup).toBeLessThan(appDataCleanup)
    expect(appDataCleanup).toBeLessThan(binaryDelete)
  })

  it('shows UTF-16 project cleanup failures before removing their report', () => {
    const source = readFileSync('src-tauri/windows/installer.nsi', 'utf8')
    const cleanup = source.indexOf('--cleanup-projects')
    const reportRead = source.indexOf('FileReadUTF16LE', cleanup)
    const detail = source.indexOf('DetailPrint "删除失败：$R1"', cleanup)
    const failureDialog = source.indexOf('完整失败列表已显示在卸载详情中', cleanup)
    const reportDelete = source.indexOf('Delete "$ProjectReportPath"', cleanup)

    expect(cleanup).toBeGreaterThan(0)
    expect(reportRead).toBeGreaterThan(cleanup)
    expect(detail).toBeGreaterThan(reportRead)
    expect(failureDialog).toBeGreaterThan(detail)
    expect(reportDelete).toBeGreaterThan(failureDialog)
  })

  it('disables destructive choices while running as an updater', () => {
    const source = readFileSync('src-tauri/windows/installer.nsi', 'utf8')
    const onInit = source.indexOf('Function un.onInit')
    const onInitEnd = source.indexOf('FunctionEnd', onInit)
    const updateGuard = source.indexOf('${If} $UpdateMode = 1', onInit)

    expect(onInit).toBeGreaterThan(0)
    expect(updateGuard).toBeGreaterThan(onInit)
    expect(updateGuard).toBeLessThan(onInitEnd)
    const appDataReset = source.indexOf('StrCpy $DeleteAppDataCheckboxState 0', updateGuard)
    const projectReset = source.indexOf('StrCpy $DeleteProjectsCheckboxState 0', updateGuard)
    expect(appDataReset).toBeGreaterThan(updateGuard)
    expect(appDataReset).toBeLessThan(onInitEnd)
    expect(projectReset).toBeGreaterThan(updateGuard)
    expect(projectReset).toBeLessThan(onInitEnd)
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
