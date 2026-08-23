import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { DESKTOP_BUNDLES, ensureDesktopProfile } from './desktop-profile.mjs'

const desktopPatchPath = 'packages/dsh-plugin-desktop/cordis.patch.yml'
const compatibleReport = {
  schemaVersion: 1,
  profileBundles: [...DESKTOP_BUNDLES],
  packages: [
    { name: '@deepseek-ai/dsh', status: 'compatible' },
    ...DESKTOP_BUNDLES.map((name) => ({ name, status: 'compatible' })),
  ],
}

function fixture(bundles: string[]) {
  const root = mkdtempSync(join(tmpdir(), 'deepseek-harness-profile-'))
  const path = join(root, 'package.json')
  writeFileSync(path, JSON.stringify({
    name: 'dsh-profile-desktop',
    private: true,
    dependencies: { '@dsh/desktop-plugin': 'file:desktop-plugin.tgz' },
    dsh: { profile: { bundles } },
  }))
  return path
}

describe('ensureDesktopProfile', () => {
  it('hard-disables browser handoff for the Desktop web runtime', () => {
    const patch = readFileSync(desktopPatchPath, 'utf8')

    expect(patch).toMatch(/- id: web-runtime[\s\S]*?config:[\s\S]*?openBrowser: false/)
  })

  it('inserts the official web app between base and Desktop plugin', () => {
    const path = fixture(['@deepseek-ai/dsh-base', '@dsh/desktop-plugin'])

    expect(ensureDesktopProfile(path, compatibleReport)).toBe(true)
    expect(JSON.parse(readFileSync(path, 'utf8')).dsh.profile.bundles).toEqual(DESKTOP_BUNDLES)
  })

  it('repairs stale ordering and preserves unrelated manifest fields', () => {
    const path = fixture(['@dsh/desktop-plugin', '@deepseek-ai/dsh-base'])
    const before = JSON.parse(readFileSync(path, 'utf8'))

    ensureDesktopProfile(path, compatibleReport)

    const after = JSON.parse(readFileSync(path, 'utf8'))
    expect(after.name).toBe(before.name)
    expect(after.dependencies).toEqual(before.dependencies)
    expect(after.dsh.profile.bundles).toEqual(DESKTOP_BUNDLES)
  })

  it('does not rewrite an already-correct profile', () => {
    const path = fixture([...DESKTOP_BUNDLES])

    expect(ensureDesktopProfile(path, compatibleReport)).toBe(false)
  })

  it('refuses to mount a profile without the exact compatible capability report', () => {
    const path = fixture([])
    const invalidReport = (value: object) => value as Parameters<typeof ensureDesktopProfile>[1]

    expect(() => ensureDesktopProfile(path, invalidReport({ schemaVersion: 1 }))).toThrow(/capabilit/i)
    expect(() => ensureDesktopProfile(path, invalidReport({ schemaVersion: 1, profileBundles: ['@deepseek-ai/dsh-base'] }))).toThrow(/capabilit/i)
  })

  it('refuses an exact bundle list without successful package capability records', () => {
    const path = fixture([])
    const invalidReport = (value: object) => value as Parameters<typeof ensureDesktopProfile>[1]

    expect(() => ensureDesktopProfile(path, invalidReport({ profileBundles: [...DESKTOP_BUNDLES] }))).toThrow(/capabilit/i)
  })
})
