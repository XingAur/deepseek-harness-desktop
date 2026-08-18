import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { DESKTOP_BUNDLES, ensureDesktopProfile } from './desktop-profile.mjs'

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
  it('inserts the official web app between base and Desktop plugin', () => {
    const path = fixture(['@deepseek-ai/dsh-base', '@dsh/desktop-plugin'])

    expect(ensureDesktopProfile(path)).toBe(true)
    expect(JSON.parse(readFileSync(path, 'utf8')).dsh.profile.bundles).toEqual(DESKTOP_BUNDLES)
  })

  it('repairs stale ordering and preserves unrelated manifest fields', () => {
    const path = fixture(['@dsh/desktop-plugin', '@deepseek-ai/dsh-base'])
    const before = JSON.parse(readFileSync(path, 'utf8'))

    ensureDesktopProfile(path)

    const after = JSON.parse(readFileSync(path, 'utf8'))
    expect(after.name).toBe(before.name)
    expect(after.dependencies).toEqual(before.dependencies)
    expect(after.dsh.profile.bundles).toEqual(DESKTOP_BUNDLES)
  })

  it('does not rewrite an already-correct profile', () => {
    const path = fixture([...DESKTOP_BUNDLES])

    expect(ensureDesktopProfile(path)).toBe(false)
  })
})
