import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { DESKTOP_BUNDLES, ensureDesktopProfile } from './desktop-profile.mjs'
import type { RuntimeCapabilityReport } from './runtime-capabilities.mjs'

const desktopPatchPath = 'packages/dsh-plugin-desktop/cordis.patch.yml'
const compatibleReport: RuntimeCapabilityReport = {
  schemaVersion: 1,
  profileBundles: [...DESKTOP_BUNDLES],
  packages: [
    { name: '@deepseek-ai/dsh', observedVersion: '0.1.1-rc.2', status: 'compatible', entrypoints: { bin: 'lib/bin.js' } },
    { name: '@deepseek-ai/dsh-base', observedVersion: '0.1.1-rc.2', status: 'compatible', entrypoints: { '.': { default: './lib/index.js', types: './lib/types/index.d.ts' }, './invariant': { default: './lib/invariant.js', types: './lib/types/invariant.d.ts' }, './cordis.patch.yml': './cordis.patch.yml', './package.json': './package.json' }, bundlePatch: './cordis.patch.yml' },
    { name: '@deepseek-ai/dsh-web-app', observedVersion: '0.1.1-rc.2', status: 'compatible', entrypoints: { '.': { default: './lib/index.js', types: './lib/types/index.d.ts' }, './invariant': { default: './lib/invariant.js', types: './lib/types/invariant.d.ts' }, './cordis.patch.yml': './cordis.patch.yml', './package.json': './package.json', './startup': { default: './lib/startup.js', types: './lib/types/startup.d.ts' } }, bundlePatch: './cordis.patch.yml' },
    { name: '@dsh/desktop-plugin', observedVersion: '0.3.2', status: 'compatible', entrypoints: { '.': './lib/index.js', './client': './lib/client.js', './package.json': './package.json' }, bundlePatch: './cordis.patch.yml' },
    ...['@deepseek-ai/dsh-llm-pi-ai', '@deepseek-ai/dsh-skill', '@deepseek-ai/dsh-mcp-client'].map((name) => ({ name, observedVersion: '0.1.1-rc.2', status: 'compatible' as const, entrypoints: { '.': { default: './lib/index.js', types: './lib/types/index.d.ts' }, './invariant': { default: './lib/invariant.js', types: './lib/types/invariant.d.ts' }, './package.json': './package.json' } })),
  ],
  capabilities: {
    apiProvider: { package: '@deepseek-ai/dsh-llm-pi-ai', available: true },
    skill: { package: '@deepseek-ai/dsh-skill', available: true },
    mcp: { package: '@deepseek-ai/dsh-mcp-client', available: true },
  },
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

  it.each([
    ['a missing schema version', (report: typeof compatibleReport) => { delete (report as { schemaVersion?: number }).schemaVersion }],
    ['a malformed required observed version', (report: typeof compatibleReport) => { report.packages[0].observedVersion = null as never }],
    ['a tampered required entrypoint', (report: typeof compatibleReport) => { (report.packages[1].entrypoints['.'] as { default: string }).default = './lib/tampered.js' }],
    ['a duplicate package record', (report: typeof compatibleReport) => { report.packages.push({ ...report.packages[0] }) }],
  ])('rejects %s before mutating the desktop profile', (_label, mutate) => {
    const path = fixture([])
    const report = JSON.parse(JSON.stringify(compatibleReport)) as typeof compatibleReport
    mutate(report)

    expect(() => ensureDesktopProfile(path, report)).toThrow(/capabilit/i)
    expect(JSON.parse(readFileSync(path, 'utf8')).dsh.profile.bundles).toEqual([])
  })
})
