import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { initializeDefaultE2ERoot } from './owned-e2e-root.mjs'

const roots: string[] = []

afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true })
})

describe('initializeDefaultE2ERoot', () => {
  it('publishes a new root marker as an exclusive ordinary file', () => {
    const parent = temporaryRoot()
    const root = join(parent, 'e2e-root')

    initializeDefaultE2ERoot(root)

    expect(readFileSync(join(root, '.dsh-e2e-root-owned'), 'utf8')).toBe('E2E-owned')
    expect(existsSync(join(root, 'e2e-artifacts'))).toBe(false)
  })

  it('reuses a root only when its marker is a safe ordinary file with exact contents', () => {
    const parent = temporaryRoot()
    const root = join(parent, 'e2e-root')
    mkdirSync(root)
    writeFileSync(join(root, '.dsh-e2e-root-owned'), 'E2E-owned', 'utf8')

    initializeDefaultE2ERoot(root)

    expect(readFileSync(join(root, '.dsh-e2e-root-owned'), 'utf8')).toBe('E2E-owned')
    expect(existsSync(join(root, 'e2e-artifacts'))).toBe(false)
  })

  it('rejects a marker link or reparse point without touching artifacts', (context) => {
    const parent = temporaryRoot()
    const root = join(parent, 'e2e-root')
    const marker = join(root, '.dsh-e2e-root-owned')
    const external = join(parent, 'external-marker')
    const artifacts = join(root, 'e2e-artifacts')
    mkdirSync(root)
    mkdirSync(artifacts)
    writeFileSync(join(artifacts, 'must-survive.txt'), 'unowned-artifacts', 'utf8')
    mkdirSync(external)
    try {
      symlinkSync(external, marker, process.platform === 'win32' ? 'junction' : 'dir')
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'EPERM') {
        context.skip('当前 Windows 权限不允许创建 junction reparse point')
        return
      }
      throw error
    }

    expect(() => initializeDefaultE2ERoot(root)).toThrow('默认 E2E root 未受本套件所有权标记保护')
    expect(readFileSync(join(artifacts, 'must-survive.txt'), 'utf8')).toBe('unowned-artifacts')
  })
})

function temporaryRoot(): string {
  const root = mkdtempSync(join(tmpdir(), 'dsh-owned-root-'))
  roots.push(root)
  return root
}
