import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const scripts = [
  'install-web-setup.ps1',
  'reset-web-setup.ps1',
  'uninstall-web-setup.ps1',
  'verify-cleanup.ps1',
] as const

describe('E2E PowerShell hidden path safety', () => {
  it.each(scripts)('%s reads every reparse-check item with -LiteralPath -Force', (name) => {
    const source = readFileSync(resolve('scripts/e2e', name), 'utf8')
    expect(source).toMatch(/Get-Item -LiteralPath \$Path -Force/)
    expect(source).not.toMatch(/Get-Item -LiteralPath \$Path\)(?!\.Attributes)/)
    expect(source).not.toMatch(/Get-Item -LiteralPath \$dataRoot\)(?!\.Attributes)/)
  })
})
