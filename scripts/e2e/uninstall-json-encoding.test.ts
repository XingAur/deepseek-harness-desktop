import { execFileSync } from 'node:child_process'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

describe('uninstall PowerShell JSON encoding', () => {
  it('在 Windows PowerShell 5.1 中以 UTF-8 读取 Node 写入的 Unicode sentinel 路径', () => {
    const root = mkdtempSync(join(tmpdir(), 'dsh-e2e-uninstall-json-'))
    const sentinels = join(root, 'preservation-sentinels.json')
    const projectPath = 'E:\\e2e\\projects-owned\\E2E 卸载 delete-all Ω-4\\e2e-preserve.txt'
    writeFileSync(sentinels, JSON.stringify({ entries: [{ scope: 'project', path: projectPath }] }), 'utf8')
    const escapedPath = sentinels.replace(/'/g, "''")
    const command = `$value = (Get-Content -LiteralPath '${escapedPath}' -Raw -Encoding UTF8 | ConvertFrom-Json).entries[0].path; [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($value))`
    const encoded = Buffer.from(command, 'utf16le').toString('base64')
    try {
      const output = execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-EncodedCommand', encoded], { encoding: 'utf8' }).trim()
      expect(Buffer.from(output, 'base64').toString('utf8')).toBe(projectPath)
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  it('脚本对安装记录与哨兵清单均显式使用 UTF-8', () => {
    const source = readFileSync(resolve('scripts/e2e/uninstall-web-setup.ps1'), 'utf8')
    expect(source).toContain('Get-Content -LiteralPath $recordFile -Raw -Encoding UTF8')
    expect(source).toContain('Get-Content -LiteralPath $sentinelsFile -Raw -Encoding UTF8')
  })
})
