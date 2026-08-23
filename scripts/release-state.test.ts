import { describe, expect, it } from 'vitest'
import { classifyReleaseState, parseBooleanArgument } from './release-state.mjs'

describe('release state recovery', () => {
  const base = { version: '0.1.13', legacyReleaseBaseline: '0.1.12' }

  it('accepts only the exact pre-manifest public baseline as complete', () => {
    expect(classifyReleaseState({
      version: '0.1.12', legacyReleaseBaseline: '0.1.12', tagExists: true,
      release: { isDraft: false, assets: [] },
    })).toMatchObject({ status: 'complete' })
  })

  it('requires a missing current tag to be recovered before another bump', () => {
    expect(classifyReleaseState({ ...base, tagExists: false, release: null })).toMatchObject({ status: 'pending-tag' })
  })

  it.each([
    null,
    { isDraft: true, assets: [] },
  ])('re-dispatches an absent or draft release', (release) => {
    expect(classifyReleaseState({ ...base, tagExists: true, release })).toMatchObject({ status: 'pending-release' })
  })

  it('accepts a public post-baseline release only with the completion marker', () => {
    expect(classifyReleaseState({
      ...base, tagExists: true,
      release: { isDraft: false, assets: [{ name: 'desktop-release.json' }] },
    })).toMatchObject({ status: 'complete' })
  })

  it('blocks automatic mutation of a public incomplete release', () => {
    expect(classifyReleaseState({
      ...base, tagExists: true,
      release: { isDraft: false, assets: [{ name: 'installer.exe' }] },
    })).toMatchObject({ status: 'blocked' })
  })

  it('rejects a release attached to a missing tag', () => {
    expect(() => classifyReleaseState({
      ...base, tagExists: false,
      release: { isDraft: false, assets: [{ name: 'desktop-release.json' }] },
    })).toThrow(/tag/i)
  })

  it('rejects attempts to move the historical completion baseline', () => {
    expect(() => classifyReleaseState({
      version: '0.1.13', legacyReleaseBaseline: '0.1.13', tagExists: true,
      release: { isDraft: false, assets: [] },
    })).toThrow(/固定为 0\.1\.12/)
  })

  it('rejects ambiguous CLI boolean values instead of treating them as false', () => {
    expect(parseBooleanArgument('true', 'tag-exists')).toBe(true)
    expect(parseBooleanArgument('false', 'tag-exists')).toBe(false)
    expect(() => parseBooleanArgument('TRUE', 'tag-exists')).toThrow(/true.*false/i)
  })
})
