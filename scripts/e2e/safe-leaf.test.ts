import { describe, expect, it } from 'vitest'
import { mkdtempSync, rmSync, symlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { assertSafeLeaf } from '../../e2e/support/safe-path'

describe('safe leaf', () => {
  it('rejects a pre-existing symlink leaf', () => { const root = mkdtempSync(join(tmpdir(), 'dsh-leaf-')); try { const target = join(root, 'target'); const leaf = join(root, 'leaf'); try { symlinkSync(target, leaf, 'file') } catch { return }; expect(() => assertSafeLeaf(leaf)).toThrow() } finally { rmSync(root, { recursive: true, force: true }) } })
})
