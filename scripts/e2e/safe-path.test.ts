import { describe, expect, it } from 'vitest'
import { lstatSync, mkdtempSync, realpathSync, rmSync, symlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { assertSafePath, prepareSafeDirectory } from '../../e2e/support/safe-path'

describe('safe paths', () => {
  it('creates a new directory below a safe temporary parent', () => { const root = mkdtempSync(join(tmpdir(), 'dsh-safe-')); try { expect(prepareSafeDirectory(join(root, 'new'))).toContain('new') } finally { rmSync(root, { recursive: true, force: true }) } })
  it('rejects a symlink ancestor when supported', () => { const root = mkdtempSync(join(tmpdir(), 'dsh-safe-')); const link = join(root, 'link'); try { try { symlinkSync(root, link, 'junction') } catch { return }; if (!lstatSync(link).isSymbolicLink() || realpathSync.native(link) === link) return; expect(() => assertSafePath(link)).toThrow() } finally { rmSync(root, { recursive: true, force: true }) } })
  it('rejects a dangling symlink ancestor when supported', () => { const root = mkdtempSync(join(tmpdir(), 'dsh-dangling-')); const link = join(root, 'dangling'); try { try { symlinkSync(join(root, 'missing-target'), link, 'file') } catch { return }; expect(() => assertSafePath(join(link, 'child'))).toThrow() } finally { rmSync(root, { recursive: true, force: true }) } })
})
