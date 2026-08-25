import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, mkdirSync, readFileSync, renameSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { removeOwnedTreeWithoutFollowingReparsePoints } from './owned-tree-cleanup.mjs'

const roots: string[] = []

afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true })
})

describe('owned PowerShell tree cleanup', () => {
  it('uses a non-recursive reparse-aware walker from reset and never rejects runtime package links', () => {
    const reset = readFileSync(resolve('scripts/e2e/reset-web-setup.ps1'), 'utf8')
    const install = readFileSync(resolve('scripts/e2e/install-web-setup.ps1'), 'utf8')
    const helper = readFileSync(resolve('scripts/e2e/owned-tree-cleanup.ps1'), 'utf8')
    const nodeHelper = readFileSync(resolve('scripts/e2e/owned-tree-cleanup.mjs'), 'utf8')

    expect(reset).toContain('. $PSScriptRoot\\owned-tree-cleanup.ps1')
    expect(reset).toContain('Assert-NoReparseComponents $dataRoot')
    expect(reset).toContain("Existing data root is a reparse point")
    expect(reset).toContain("Existing data root lacks .dsh-e2e-owned; manual cleanup is required")
    expect(reset).toContain('function Assert-OwnedDataRootMarker')
    expect(reset).toContain('Get-Item -LiteralPath $Marker -Force')
    expect(reset).toContain('$before -isnot [System.IO.FileInfo]')
    expect(reset).toContain('$before.PSIsContainer')
    expect(reset).toContain('[System.IO.File]::ReadAllText($Marker')
    expect(reset).toContain("$contents -cne 'E2E-owned'")
    expect(reset.indexOf('Assert-OwnedDataRootMarker $marker')).toBeLessThan(reset.indexOf('Remove-OwnedTreeWithoutFollowingReparsePoints $dataRoot'))
    expect(reset).toContain('Remove-OwnedTreeWithoutFollowingReparsePoints $dataRoot')
    expect(reset).not.toContain('Assert-NoNestedReparsePoints')
    expect(reset).not.toMatch(/Remove-Item -LiteralPath \$dataRoot -Recurse/)
    expect(install).not.toContain('Assert-NoNestedReparsePoints')
    expect(helper).toContain("Get-Command node.exe -ErrorAction Stop")
    expect(helper).toContain("owned-tree-cleanup.mjs') '--root'")
    expect(nodeHelper).toContain("from 'node:fs/promises'")
    expect(nodeHelper).toContain('const rootMetadata = await checkedLstat(context, cursor)')
    expect(nodeHelper).toContain('async function checkDirectChildren(context, parent, entries)')
    expect(nodeHelper).toContain('await assertOwnedTreePathWithoutReparsePoints(context, parent, false)')
    expect(nodeHelper).toContain('beforeScheduleDirectChildren')
    expect(nodeHelper).not.toContain('Promise.all(paths.map((cursor) => limited(context, () => lstat(cursor))))')
    expect(nodeHelper).toContain('const entries = await limited(context, () => readdir(path))')
    expect(nodeHelper).toContain('new Semaphore(16)')
    expect(nodeHelper).toContain('const precheckedChildren = await checkDirectChildren(context, path, children)')
    expect(nodeHelper).toContain('await Promise.all(precheckedChildren.map')
    expect(nodeHelper).toContain('rm(path, { force: false, recursive: false')
    expect(nodeHelper).toContain('const marker = path === context.rootPath ? context.markerName : undefined')
    expect(nodeHelper).toContain("entry.toLowerCase() === OWNERSHIP_MARKER")
    expect(nodeHelper).toContain("contents !== 'E2E-owned'")
    expect(nodeHelper).toContain('context.markerName = await assertOwnedRootMarker(context)')
    expect(nodeHelper.match(/assertOwnedTreePathWithoutReparsePoints\(context, path/g)?.length).toBeGreaterThanOrEqual(5)
  })

  it('deletes an owned-tree junction entry without traversing its external target', (context) => {
    if (process.platform !== 'win32') context.skip('junction 行为仅在 Windows 上验证')
    const root = temporaryRoot()
    const helper = resolve('scripts/e2e/owned-tree-cleanup.ps1').replace(/'/g, "''")
    const output = execFileSync('powershell.exe', [
      '-NoProfile', '-NonInteractive', '-Command',
      [
        "$ErrorActionPreference = 'Stop'",
        `. '${helper}'`,
        `$root = '${join(root, 'owned').replace(/'/g, "''")}'`,
        `$outside = '${join(root, 'outside').replace(/'/g, "''")}'`,
        '$junction = Join-Path $root \'runtime-link\'',
        '[IO.Directory]::CreateDirectory($root) | Out-Null',
        "[IO.File]::WriteAllText((Join-Path $root '.dsh-e2e-owned'), 'E2E-owned')",
        '[IO.Directory]::CreateDirectory($outside) | Out-Null',
        "[IO.File]::WriteAllText((Join-Path $outside 'keep.txt'), 'keep')",
        '& cmd.exe /d /c mklink /J $junction $outside | Out-Null',
        "if ($LASTEXITCODE -ne 0) { 'junction-unavailable'; exit 0 }",
        'Remove-OwnedTreeWithoutFollowingReparsePoints $root',
        "if (Test-Path -LiteralPath $root) { throw 'owned root still exists' }",
        "if ((Get-Content -LiteralPath (Join-Path $outside 'keep.txt') -Raw) -ne 'keep') { throw 'outside target changed' }",
        "'deleted'",
      ].join('; '),
    ], { encoding: 'utf8', windowsHide: true }).trim()
    if (output === 'junction-unavailable') context.skip('当前 Windows 环境不允许创建 junction')
    expect(output).toBe('deleted')
  })

  it('rejects a directory replaced by a junction immediately before enumeration', async (context) => {
    if (process.platform !== 'win32') context.skip('junction 行为仅在 Windows 上验证')
    const root = temporaryRoot()
    const owned = join(root, 'owned')
    const outside = join(root, 'outside')
    const nested = join(owned, 'runtime')
    const moved = join(owned, 'runtime-before-replace')
    mkdirSync(nested, { recursive: true })
    const markerName = '.DSH-E2E-OWNED'
    writeFileSync(join(owned, markerName), 'E2E-owned', 'utf8')
    mkdirSync(outside)
    writeFileSync(join(outside, 'keep.txt'), 'keep', 'utf8')
    let replaced = false
    await expect(removeOwnedTreeWithoutFollowingReparsePoints(owned, {
      beforeEnumerate: async (path) => {
        if (path !== nested || replaced) return
        replaced = true
        renameSync(nested, moved)
        try {
          symlinkSync(outside, nested, 'junction')
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code === 'EPERM') context.skip('当前 Windows 环境不允许创建 junction')
          throw error
        }
      },
    })).rejects.toThrow('reparse point')
    expect(replaced).toBe(true)
    expect(readFileSync(join(outside, 'keep.txt'), 'utf8')).toBe('keep')
    expect(existsSync(outside)).toBe(true)
    expect(readFileSync(join(owned, markerName), 'utf8')).toBe('E2E-owned')
  })

  it('does not schedule a direct child after its verified parent is replaced by a junction', async (context) => {
    if (process.platform !== 'win32') context.skip('junction 行为仅在 Windows 上验证')
    const root = temporaryRoot()
    const owned = join(root, 'owned')
    const outside = join(root, 'outside')
    const runtime = join(owned, 'runtime')
    const moved = join(owned, 'runtime-before-schedule-replace')
    mkdirSync(runtime, { recursive: true })
    writeFileSync(join(owned, '.dsh-e2e-owned'), 'E2E-owned', 'utf8')
    writeFileSync(join(runtime, 'inside.txt'), 'inside', 'utf8')
    mkdirSync(outside)
    writeFileSync(join(outside, 'must-not-lstat.txt'), 'outside', 'utf8')
    const directChild = resolve(runtime, 'inside.txt')
    const observedLstats: string[] = []
    let replaced = false

    await expect(removeOwnedTreeWithoutFollowingReparsePoints(owned, {
      beforeScheduleDirectChildren: async (path) => {
        if (path !== runtime || replaced) return
        replaced = true
        renameSync(runtime, moved)
        try {
          symlinkSync(outside, runtime, 'junction')
        } catch (error) {
          if ((error as NodeJS.ErrnoException).code === 'EPERM') context.skip('当前 Windows 环境不允许创建 junction')
          throw error
        }
      },
      onLstat: (path) => observedLstats.push(resolve(path)),
    })).rejects.toThrow('reparse point')

    expect(replaced).toBe(true)
    // onLstat observes lexical arguments, not Windows junction resolution.
    // The relevant safety contract is that revalidation rejects before the
    // direct child work item can be scheduled at all.
    expect(observedLstats).not.toContain(directChild)
    expect(readFileSync(join(outside, 'must-not-lstat.txt'), 'utf8')).toBe('outside')
  })

  it('refuses an invalid ownership marker before touching payload files', async () => {
    const root = join(temporaryRoot(), 'owned')
    mkdirSync(root, { recursive: true })
    writeFileSync(join(root, '.dsh-e2e-owned'), 'not-owned', 'utf8')
    const payload = join(root, 'runtime', 'keep-until-authorized.txt')
    mkdirSync(join(root, 'runtime'))
    writeFileSync(payload, 'keep', 'utf8')

    await expect(removeOwnedTreeWithoutFollowingReparsePoints(root)).rejects.toThrow('ownership marker is invalid')
    expect(readFileSync(payload, 'utf8')).toBe('keep')
  })

  it('retains a marker renamed only by case after initial validation until final cleanup', async () => {
    const root = join(temporaryRoot(), 'owned')
    const originalMarker = join(root, '.dsh-e2e-owned')
    const renamedMarker = join(root, '.DSH-E2E-OWNED')
    const payloadDirectory = join(root, 'runtime')
    mkdirSync(payloadDirectory, { recursive: true })
    writeFileSync(originalMarker, 'E2E-owned', 'utf8')
    writeFileSync(join(payloadDirectory, 'payload.js'), 'x', 'utf8')
    let renamed = false
    let markerRetainedDuringPayload = false

    await removeOwnedTreeWithoutFollowingReparsePoints(root, {
      beforeEnumerate: async (path) => {
        if (path === root && !renamed) {
          renameSync(originalMarker, renamedMarker)
          renamed = true
        }
        if (path === payloadDirectory) markerRetainedDuringPayload = existsSync(renamedMarker)
      },
    })

    expect(renamed).toBe(true)
    expect(markerRetainedDuringPayload).toBe(true)
    expect(existsSync(root)).toBe(false)
  })

  it('cleans a 10,000-file runtime-shaped tree within a bounded interval', async () => {
    const root = join(temporaryRoot(), 'owned')
    mkdirSync(root, { recursive: true })
    writeFileSync(join(root, '.dsh-e2e-owned'), 'E2E-owned', 'utf8')
    for (let directory = 0; directory < 200; directory += 1) {
      const current = join(root, 'runtime', 'node_modules', `package-${directory}`)
      mkdirSync(current, { recursive: true })
      for (let file = 0; file < 50; file += 1) writeFileSync(join(current, `file-${file}.js`), 'module.exports = 1\n', 'utf8')
    }

    const started = performance.now()
    await removeOwnedTreeWithoutFollowingReparsePoints(root)
    const elapsedMs = performance.now() - started

    expect(existsSync(root)).toBe(false)
    expect(elapsedMs).toBeLessThan(30_000)
  }, 30_000)

  it('limits concurrent native filesystem operations across nested directories to 16', async () => {
    const root = join(temporaryRoot(), 'owned')
    mkdirSync(root, { recursive: true })
    writeFileSync(join(root, '.dsh-e2e-owned'), 'E2E-owned', 'utf8')
    for (let directory = 0; directory < 32; directory += 1) {
      const nested = join(root, 'runtime', `candidate-${directory}`, 'node_modules')
      mkdirSync(nested, { recursive: true })
      for (let file = 0; file < 8; file += 1) writeFileSync(join(nested, `file-${file}.js`), 'x', 'utf8')
    }
    let active = 0
    let maximum = 0
    await removeOwnedTreeWithoutFollowingReparsePoints(root, {
      onOperationStart: () => { active += 1; maximum = Math.max(maximum, active) },
      onOperationEnd: () => { active -= 1 },
    })
    expect(active).toBe(0)
    expect(maximum).toBeGreaterThan(1)
    expect(maximum).toBeLessThanOrEqual(16)
  })
})

function temporaryRoot(): string {
  const root = mkdtempSync(join(tmpdir(), 'dsh-owned-tree-'))
  roots.push(root)
  return root
}
