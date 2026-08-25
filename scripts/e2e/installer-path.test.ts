import { afterEach, describe, expect, it } from 'vitest'
import { resolve } from 'node:path'
import { resolveInstallerPath } from '../../e2e/support/installer'

const previousInstaller = process.env.DSH_E2E_INSTALLER

afterEach(() => {
  if (previousInstaller === undefined) delete process.env.DSH_E2E_INSTALLER
  else process.env.DSH_E2E_INSTALLER = previousInstaller
})

describe('installer artifact selection', () => {
  const candidate = resolve('candidate.exe')

  it('keeps an explicit environment installer ahead of candidate metadata', () => {
    const explicit = resolve('explicit-installer.exe')
    process.env.DSH_E2E_INSTALLER = explicit
    expect(resolveInstallerPath({ installers: { candidate: { path: candidate, version: '1.0.0', sha256: 'a'.repeat(64) } } })).toBe(explicit)
  })

  it('prefers an explicit option over environment and candidate', () => {
    const explicit = resolve('option-installer.exe')
    process.env.DSH_E2E_INSTALLER = resolve('environment-installer.exe')
    expect(resolveInstallerPath({ installer: explicit, installers: { candidate: { path: candidate, version: '1.0.0', sha256: 'a'.repeat(64) } } })).toBe(explicit)
  })

  it('falls back to the candidate when no override exists', () => {
    delete process.env.DSH_E2E_INSTALLER
    expect(resolveInstallerPath({ installers: { candidate: { path: candidate, version: '1.0.0', sha256: 'a'.repeat(64) } } })).toBe(candidate)
  })

  it('rejects a relative or missing installer path', () => {
    delete process.env.DSH_E2E_INSTALLER
    expect(() => resolveInstallerPath({ installer: 'relative.exe' })).toThrow()
    expect(() => resolveInstallerPath()).toThrow()
  })
})
