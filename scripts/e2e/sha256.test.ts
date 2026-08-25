import { createHash } from 'node:crypto'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { execFileSync } from 'node:child_process'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const helper = resolve('scripts/e2e/sha256.ps1')
const uninstall = resolve('scripts/e2e/uninstall-web-setup.ps1')

describe('E2E SHA-256 PowerShell compatibility', () => {
  it('在 Windows PowerShell 和 PowerShell 7 中均可解析，且 fallback 得到同一小写哈希', () => {
    const root = mkdtempSync(join(tmpdir(), 'dsh-e2e-sha256-'))
    const fixture = join(root, 'fixture.bin')
    const payload = Buffer.alloc(1024 * 1024 + 17, 0x5a)
    writeFileSync(fixture, payload)
    const expected = createHash('sha256').update(payload).digest('hex')
    const escapedHelper = helper.replace(/'/g, "''")
    const escapedFixture = fixture.replace(/'/g, "''")
    try {
      for (const shell of ['powershell.exe', 'pwsh']) {
        const output = execFileSync(shell, [
          '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command',
          `. '${escapedHelper}'; $normal = Get-E2ESha256 -LiteralPath '${escapedFixture}'; function Find-E2EFileHashCommand { return $null }; $fallback = Get-E2ESha256 -LiteralPath '${escapedFixture}'; Write-Output ($normal + '|' + $fallback)`,
        ], { encoding: 'utf8' }).trim()
        expect(output).toBe(`${expected}|${expected}`)
      }
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  it('卸载脚本只经共享 helper 验证哨兵，并保持流式 fallback', () => {
    const helperSource = readFileSync(helper, 'utf8')
    const uninstallSource = readFileSync(uninstall, 'utf8')
    expect(uninstallSource).toContain('. $PSScriptRoot\\sha256.ps1')
    expect(uninstallSource).not.toMatch(/Get-FileHash/)
    expect(uninstallSource.match(/Get-E2ESha256 -LiteralPath/g)).toHaveLength(4)
    expect(helperSource).toContain('Get-Command -Name Get-FileHash -CommandType Cmdlet')
    expect(helperSource).toContain('$sha256.ComputeHash($stream)')
    expect(helperSource).not.toContain('ReadAllBytes')
    expect(helperSource).toMatch(/^[\x00-\x7F]*$/)
  })
})
