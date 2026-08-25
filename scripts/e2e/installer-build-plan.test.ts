import { describe, expect, it } from 'vitest'
import { resolve } from 'node:path'
import { createInstallerBuildPlan, deriveBaselineVersion, resolveRuntimeVersion } from './installer-build-plan.mjs'

describe('installer build plan', () => {
  it('derives the previous patch version', () => {
    expect(deriveBaselineVersion('0.1.26')).toBe('0.1.25')
  })

  it('borrows the previous minor version when patch is zero', () => {
    expect(deriveBaselineVersion('0.1.0')).toBe('0.0.65535')
  })

  it('rejects zero as a baseline source', () => {
    expect(() => deriveBaselineVersion('0.0.0')).toThrow('无法派生更低的基线版本')
  })

  it('derives huge numeric segments exactly without rounding', () => {
    expect(deriveBaselineVersion('9007199254740992.9007199254740993.9007199254740994')).toBe('9007199254740992.9007199254740993.9007199254740993')
  })

  it('accepts only three-part numeric desktop versions', () => {
    expect(() => deriveBaselineVersion('0.1')).toThrow('桌面版本不是三段数字 SemVer：0.1')
    expect(() => deriveBaselineVersion('v0.1.26')).toThrow('桌面版本不是三段数字 SemVer：v0.1.26')
    expect(() => deriveBaselineVersion('0.1.26-preview')).toThrow('桌面版本不是三段数字 SemVer：0.1.26-preview')
  })

  it('uses only the candidate variant for quick builds', () => {
    const plan = createInstallerBuildPlan({ mode: 'quick', candidateVersion: '0.1.26', artifactsRoot: './artifacts' })
    expect(plan.mode).toBe('quick')
    expect(plan.variants.map(({ name, version }) => ({ name, version }))).toEqual([
      { name: 'candidate', version: '0.1.26' },
    ])
  })

  it.each([
    '0.1',
    'v0.1.26',
  ])('rejects invalid quick candidate version %s before building', (candidateVersion) => {
    expect(() => createInstallerBuildPlan({ mode: 'quick', candidateVersion, artifactsRoot: './artifacts' })).toThrow()
  })

  it('allows zero candidate versions for quick builds', () => {
    const plan = createInstallerBuildPlan({ mode: 'quick', candidateVersion: '0.0.0', artifactsRoot: './artifacts' })
    expect(plan.variants.map(({ name, version }) => ({ name, version }))).toEqual([
      { name: 'candidate', version: '0.0.0' },
    ])
  })

  it('builds baseline before candidate for full builds', () => {
    const plan = createInstallerBuildPlan({ mode: 'full', candidateVersion: '0.1.26', artifactsRoot: './artifacts' })
    expect(plan.variants.map(({ name, version }) => ({ name, version }))).toEqual([
      { name: 'baseline', version: '0.1.25' },
      { name: 'candidate', version: '0.1.26' },
    ])
  })

  it('normalizes the artifact root and uses stable installer names', () => {
    const plan = createInstallerBuildPlan({ mode: 'full', candidateVersion: '2.4.0', artifactsRoot: './artifacts/../artifacts' })
    const artifactsRoot = resolve('./artifacts')
    expect(plan.variants.map(({ configPath }) => configPath)).toEqual([
      resolve(artifactsRoot, 'tauri-baseline.json'),
      resolve(artifactsRoot, 'tauri-candidate.json'),
    ])
    expect(plan.variants[0].installerPath).toMatch(/DeepSeek-Harness-Desktop-E2E-baseline-x64\.exe$/)
    expect(plan.variants[1].installerPath).toMatch(/DeepSeek-Harness-Desktop-E2E-candidate-x64\.exe$/)
  })

  it('prefers a non-empty runtime version override', () => {
    expect(resolveRuntimeVersion('2.0.0-preview', '1.0.0-preview')).toBe('2.0.0-preview')
    expect(resolveRuntimeVersion('', '1.0.0-preview')).toBe('1.0.0-preview')
    expect(resolveRuntimeVersion(undefined, '1.0.0-preview')).toBe('1.0.0-preview')
    expect(() => resolveRuntimeVersion(undefined, '')).toThrow('Runtime 版本不能为空')
  })
})
