import { describe, expect, it } from 'vitest'
import { assertReleaseVersionConsistency, loadReleaseVersions, validateReleaseVersions } from './release-versions.mjs'

describe('release versions', () => {
  it('keeps every derived desktop version aligned with the tracked source', () => {
    expect(assertReleaseVersionConsistency()).toEqual(loadReleaseVersions())
  })

  it('accepts only the exact release schema', () => {
    expect(validateReleaseVersions({
      schemaVersion: 2,
      desktopVersion: '1.2.3',
      runtimeVersion: '4.5.6-preview',
      dshVersion: '0.1.0-rc.8',
      dshUpstream: {
        repository: 'https://github.com/deepseek-ai/deepseek-harness.git',
        tag: 'dsh-v0.1.2-alpha.1',
        commit: 'cd5ef8148158c3a752a658978873241fdf8e2bbc',
      },
      nodeVersion: '24.14.0',
      pnpmVersion: '11.7.0',
      legacyReleaseBaseline: '0.1.12',
    })).toEqual({
      schemaVersion: 2,
      desktopVersion: '1.2.3',
      runtimeVersion: '4.5.6-preview',
      dshVersion: '0.1.0-rc.8',
      dshUpstream: {
        repository: 'https://github.com/deepseek-ai/deepseek-harness.git',
        tag: 'dsh-v0.1.2-alpha.1',
        commit: 'cd5ef8148158c3a752a658978873241fdf8e2bbc',
      },
      nodeVersion: '24.14.0',
      pnpmVersion: '11.7.0',
      legacyReleaseBaseline: '0.1.12',
    })
  })

  it.each([
    ['desktop range', { desktopVersion: '^0.1.12' }],
    ['mutable runtime', { runtimeVersion: 'latest' }],
    ['mutable DSH', { dshVersion: 'latest' }],
    ['unofficial upstream', { dshUpstream: { repository: 'https://github.com/example/deepseek-harness.git', tag: 'dsh-v0.1.2-alpha.1', commit: 'cd5ef8148158c3a752a658978873241fdf8e2bbc' } }],
    ['mutable upstream tag', { dshUpstream: { repository: 'https://github.com/deepseek-ai/deepseek-harness.git', tag: 'master', commit: 'cd5ef8148158c3a752a658978873241fdf8e2bbc' } }],
    ['invalid upstream commit', { dshUpstream: { repository: 'https://github.com/deepseek-ai/deepseek-harness.git', tag: 'dsh-v0.1.2-alpha.1', commit: 'cd5ef814' } }],
    ['short Node', { nodeVersion: '24' }],
    ['short pnpm', { pnpmVersion: '11' }],
    ['changed legacy baseline', { legacyReleaseBaseline: '0.1.13' }],
    ['unknown key', { extra: true }],
  ])('rejects %s values', (_label, override) => {
    expect(() => validateReleaseVersions({
      schemaVersion: 2,
      desktopVersion: '0.1.12',
      runtimeVersion: '0.1.9-preview',
      dshVersion: '0.1.0-rc.8',
      dshUpstream: {
        repository: 'https://github.com/deepseek-ai/deepseek-harness.git',
        tag: 'dsh-v0.1.2-alpha.1',
        commit: 'cd5ef8148158c3a752a658978873241fdf8e2bbc',
      },
      nodeVersion: '24.14.0',
      pnpmVersion: '11.7.0',
      legacyReleaseBaseline: '0.1.12',
      ...override,
    })).toThrow(/release versions/i)
  })
})
