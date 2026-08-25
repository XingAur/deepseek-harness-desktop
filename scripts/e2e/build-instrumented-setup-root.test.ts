import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { assertInstallerSuiteReady } from './run-installer-suite.mjs'
import { initializeOwnedE2EPaths, initializeOwnedE2ERoot } from './owned-e2e-root.mjs'

const roots: string[] = []

afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true })
})

describe('instrumented setup explicit E2E root ownership', () => {
  it('uses the same owned root-and-artifacts initializer before artifacts setup', () => {
    const source = readFileSync(resolve('scripts/e2e/build-instrumented-setup.mjs'), 'utf8')
    expect(source).toContain('initializeOwnedE2EPaths(e2eRoot, artifacts)')
    expect(source).toContain('validateOwnedE2EPaths({ e2eRoot, artifactsRoot })')
    expect(source).toContain('assertSafeExistingE2EPath(runtimeOutput)')
    expect(source).toContain('assertRuntimeOutputAbsent(runtimeOutput)')
  })

  it('prepares a new explicit root that runner accepts once setup metadata is present', () => {
    const cwd = temporaryRoot()
    const e2eRoot = join(cwd, 'explicit-root')
    const artifactsRoot = join(e2eRoot, 'e2e-artifacts')
    initializeOwnedE2EPaths(e2eRoot, artifactsRoot)

    expect(() => assertInstallerSuiteReady('quick', {
      cwd,
      env: { DSH_E2E_ROOT: e2eRoot, DSH_E2E_ARTIFACTS: artifactsRoot, DSH_E2E_MODE: 'quick' },
      readFile: () => JSON.stringify({ mode: 'quick' }),
      exists: () => true,
    })).not.toThrow()
  })

  it('reuses an explicit root only after its normal ownership marker validates', () => {
    const cwd = temporaryRoot()
    const e2eRoot = join(cwd, 'explicit-owned')
    mkdirSync(e2eRoot, { recursive: true })
    writeFileSync(join(e2eRoot, '.dsh-e2e-root-owned'), 'E2E-owned', 'utf8')

    expect(initializeOwnedE2ERoot(e2eRoot)).toBe(resolve(e2eRoot))
    expect(readFileSync(join(e2eRoot, '.dsh-e2e-root-owned'), 'utf8')).toBe('E2E-owned')
  })

  it('rejects an existing explicit root without a marker before artifacts can be initialized', () => {
    const parent = temporaryRoot()
    const e2eRoot = join(parent, 'explicit-root')
    const artifactsRoot = join(e2eRoot, 'e2e-artifacts')
    mkdirSync(artifactsRoot, { recursive: true })
    writeFileSync(join(artifactsRoot, 'must-survive.txt'), 'unowned-artifacts', 'utf8')

    expect(() => initializeOwnedE2ERoot(e2eRoot)).toThrow('默认 E2E root 未受本套件所有权标记保护')
    expect(existsSync(join(e2eRoot, '.dsh-e2e-root-owned'))).toBe(false)
    expect(readFileSync(join(artifactsRoot, 'must-survive.txt'), 'utf8')).toBe('unowned-artifacts')
  })
})

function temporaryRoot(): string {
  const root = mkdtempSync(join(tmpdir(), 'dsh-explicit-root-'))
  roots.push(root)
  return root
}
