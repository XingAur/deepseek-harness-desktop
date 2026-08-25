import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { assertSafeExistingE2EPath, initializeDefaultE2ERoot, initializeOwnedE2EPaths } from './owned-e2e-root.mjs'

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

  it('publishes root and artifacts markers when both paths are new', () => {
    const parent = temporaryRoot()
    const root = join(parent, 'e2e-root')
    const artifacts = join(root, 'e2e-artifacts')

    expect(initializeOwnedE2EPaths(root, artifacts)).toEqual({ e2eRoot: root, artifactsRoot: artifacts })
    expect(readFileSync(join(root, '.dsh-e2e-root-owned'), 'utf8')).toBe('E2E-owned')
    expect(readFileSync(join(artifacts, '.dsh-e2e-artifacts-owned'), 'utf8')).toBe('E2E-owned')
  })

  it('reuses root and artifacts only after both exact markers validate', () => {
    const parent = temporaryRoot()
    const root = join(parent, 'e2e-root')
    const artifacts = join(root, 'e2e-artifacts')
    initializeOwnedE2EPaths(root, artifacts)

    expect(initializeOwnedE2EPaths(root, artifacts)).toEqual({ e2eRoot: root, artifactsRoot: artifacts })
  })

  it('refuses an existing unmarked artifacts directory without adopting its contents', () => {
    const parent = temporaryRoot()
    const root = join(parent, 'e2e-root')
    const artifacts = join(root, 'e2e-artifacts')
    initializeDefaultE2ERoot(root)
    mkdirSync(artifacts)
    const sentinel = join(artifacts, 'must-survive.txt')
    writeFileSync(sentinel, 'unowned-artifacts', 'utf8')

    expect(() => initializeOwnedE2EPaths(root, artifacts)).toThrow('E2E artifacts root 未受本套件所有权标记保护')
    expect(readFileSync(sentinel, 'utf8')).toBe('unowned-artifacts')
    expect(existsSync(join(artifacts, '.dsh-e2e-artifacts-owned'))).toBe(false)
  })

  it('rejects a runtime output junction before cleanup and leaves its external target intact', (context) => {
    const parent = temporaryRoot()
    const root = join(parent, 'e2e-root')
    const artifacts = join(root, 'e2e-artifacts')
    const runtimeOutput = join(artifacts, 'runtime-build-windows-x86_64')
    const external = join(parent, 'external-runtime')
    initializeOwnedE2EPaths(root, artifacts)
    mkdirSync(external)
    const sentinel = join(external, 'must-survive.txt')
    writeFileSync(sentinel, 'outside', 'utf8')
    try {
      symlinkSync(external, runtimeOutput, process.platform === 'win32' ? 'junction' : 'dir')
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'EPERM') {
        context.skip('当前 Windows 权限不允许创建 junction reparse point')
        return
      }
      throw error
    }

    expect(() => assertSafeExistingE2EPath(runtimeOutput)).toThrow('不安全路径')
    expect(readFileSync(sentinel, 'utf8')).toBe('outside')
    expect(existsSync(external)).toBe(true)
  })
})

function temporaryRoot(): string {
  const root = mkdtempSync(join(tmpdir(), 'dsh-owned-root-'))
  roots.push(root)
  return root
}
