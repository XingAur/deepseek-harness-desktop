import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const installer = readFileSync(resolve('e2e/support/installer.ts'), 'utf8')
const uninstall = readFileSync(resolve('scripts/e2e/uninstall-web-setup.ps1'), 'utf8')

describe('uninstall project sentinel ownership contract', () => {
  it('创建的项目根标记与卸载前验证使用同一普通、精确内容的 marker', () => {
    expect(installer).toContain("join(projectPath, '.dsh-e2e-project-owned')")
    expect(installer).toContain("writeFileSync(join(projectPath, '.dsh-e2e-project-owned'), 'E2E-owned', 'utf8')")
    expect(uninstall).toContain('function Assert-OwnedProjectMarker')
    expect(uninstall).toContain('Get-Item -LiteralPath $markerPath -Force -ErrorAction Stop')
    expect(uninstall).toContain('$ProjectMarker.Length -ne 9')
    expect(uninstall).toContain('Read-E2EOwnedProjectMarker $markerPath')
    expect(uninstall).toContain('$projectMarkerAfterRead = Get-Item -LiteralPath $markerPath -Force -ErrorAction Stop')
    expect(uninstall).toContain('Get-ProjectMarkerSignature $projectMarkerAfterRead')
    expect(uninstall).not.toContain("Test-Path -LiteralPath $projectMarker -PathType Leaf")
  })

  it.skipIf(process.platform !== 'win32')('以 -Force 读取隐藏的有效 marker，并拒绝内容被篡改的 marker', () => {
    const root = mkdtempSync(join(tmpdir(), 'dsh-e2e-project-marker-'))
    const project = join(root, 'project')
    const marker = join(project, '.dsh-e2e-project-owned')
    const escapedScript = resolve('scripts/e2e/uninstall-web-setup.ps1').replace(/'/g, "''")
    const escapedProject = project.replace(/'/g, "''")
    try {
      mkdirSync(project)
      writeFileSync(marker, 'E2E-owned', { encoding: 'utf8', flag: 'wx' })
      const command = `$ErrorActionPreference = 'Stop'; $source = [System.IO.File]::ReadAllText('${escapedScript}'); $start = $source.IndexOf('function Assert-ExactProjectMarker'); $end = $source.IndexOf('function Get-LocalAppData'); function Assert-NoReparseComponents([string]$Path) {}; Invoke-Expression $source.Substring($start, $end - $start); $project = '${escapedProject}'; New-Item -ItemType Directory -Force -Path $project | Out-Null; $marker = Join-Path $project '.dsh-e2e-project-owned'; $utf8 = New-Object System.Text.UTF8Encoding($false); [System.IO.File]::WriteAllText($marker, 'E2E-owned', $utf8); [System.IO.File]::SetAttributes($marker, [System.IO.FileAttributes]::Hidden); Assert-OwnedProjectMarker $project; [System.IO.File]::SetAttributes($marker, [System.IO.FileAttributes]::Normal); [System.IO.File]::WriteAllText($marker, 'tampered', $utf8); try { Assert-OwnedProjectMarker $project; throw 'tampered marker accepted' } catch { if ($_.Exception.Message -eq 'tampered marker accepted') { throw } }; 'marker-contract-ok'`
      const output = execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', command], { encoding: 'utf8' })
      expect(output.trim()).toBe('marker-contract-ok')
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  }, 30_000)

  it.skipIf(process.platform !== 'win32')('读取期间 marker 被替换为 junction 时必须拒绝', (context) => {
    const root = mkdtempSync(join(tmpdir(), 'dsh-e2e-project-marker-race-'))
    const project = join(root, 'project')
    const marker = join(project, '.dsh-e2e-project-owned')
    const target = join(root, 'junction-target')
    const escapedScript = resolve('scripts/e2e/uninstall-web-setup.ps1').replace(/'/g, "''")
    const escapedProject = project.replace(/'/g, "''")
    const escapedTarget = target.replace(/'/g, "''")
    try {
      mkdirSync(project)
      mkdirSync(target)
      writeFileSync(marker, 'E2E-owned', { encoding: 'utf8', flag: 'wx' })
      const command = `$ErrorActionPreference = 'Stop'; $source = [System.IO.File]::ReadAllText('${escapedScript}'); $start = $source.IndexOf('function Assert-ExactProjectMarker'); $end = $source.IndexOf('function Get-LocalAppData'); function Assert-NoReparseComponents([string]$Path) {}; Invoke-Expression $source.Substring($start, $end - $start); $project = '${escapedProject}'; $target = '${escapedTarget}'; $script:junctionUnavailable = $false; function Read-E2EOwnedProjectMarker([string]$MarkerPath) { [System.IO.File]::Delete($MarkerPath); & cmd.exe /c ('mklink /J "' + $MarkerPath + '" "' + $target + '"') | Out-Null; if ($LASTEXITCODE -ne 0) { $script:junctionUnavailable = $true }; return 'E2E-owned' }; try { Assert-OwnedProjectMarker $project; 'race-accepted' } catch { if ($script:junctionUnavailable) { 'junction-unavailable' } elseif ($_.Exception.Message -eq 'Project sentinel ownership marker changed while being read' -or $_.Exception.Message -eq 'Project sentinel has an invalid ownership marker') { 'race-rejected' } else { throw } }`
      const output = execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command', command], { encoding: 'utf8' }).trim()
      if (output === 'junction-unavailable') context.skip('当前 Windows 权限不允许创建 junction')
      expect(output).toBe('race-rejected')
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })
})
