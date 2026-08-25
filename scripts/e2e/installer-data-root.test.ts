import { describe, expect, it } from 'vitest'
import { mkdtempSync, existsSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { WindowsInstallerHarness } from '../../e2e/support/installer'

describe('installer data root guard', () => {
  it('rejects a tampered safe-looking record root before writes', async () => {
    const root = mkdtempSync(join(tmpdir(), 'dsh-record-'))
    try {
      const tampered = resolve(root, 'safe-looking')
      const harness = new WindowsInstallerHarness({ root, artifactRoot: root, installer: join(root, 'dsh-installer.exe') }) as unknown as { latest: unknown; writePreservationSentinels: () => Promise<unknown> }
      harness.latest = { dataRoot: tampered }
      await expect(harness.writePreservationSentinels()).rejects.toThrow()
      expect(existsSync(tampered)).toBe(false)
      expect(existsSync(join(root, 'projects-owned', 'preserved-project'))).toBe(false)
    } finally { rmSync(root, { recursive: true, force: true }) }
  })
})
