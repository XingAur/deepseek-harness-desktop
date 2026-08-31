import { createHash } from 'node:crypto'
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { DESKTOP_BUNDLES, ensureDesktopProfile } from './desktop-profile.mjs'
import type { RuntimeCapabilityReport } from './runtime-capabilities.mjs'

const desktopPatchPath = 'packages/dsh-plugin-desktop/cordis.patch.yml'
const closurePackages = [
  '@deepseek-ai/dsh-base',
  '@deepseek-ai/dsh-mcp-client',
  '@deepseek-ai/dsh-skill',
  '@deepseek-ai/dsh-web-app',
].map((name) => ({ name, declaredRange: '^0.1.1-rc.2', observedVersion: '0.1.1-rc.2', license: 'MIT', entrypoint: true as const, status: 'compatible' as const }))
const compatibleReport: RuntimeCapabilityReport = {
  schemaVersion: 2,
  profileBundles: [...DESKTOP_BUNDLES],
  packages: [
    { name: '@deepseek-ai/dsh', observedVersion: '0.1.1-rc.2', status: 'compatible', entrypoints: { bin: 'lib/bin.js' } },
    { name: '@deepseek-ai/dsh-base', observedVersion: '0.1.1-rc.2', status: 'compatible', entrypoints: { '.': { default: './lib/index.js', types: './lib/types/index.d.ts' }, './invariant': { default: './lib/invariant.js', types: './lib/types/invariant.d.ts' }, './cordis.patch.yml': './cordis.patch.yml', './package.json': './package.json' }, bundlePatch: './cordis.patch.yml' },
    { name: '@deepseek-ai/dsh-web-app', observedVersion: '0.1.1-rc.2', status: 'compatible', entrypoints: { '.': { default: './lib/index.js', types: './lib/types/index.d.ts' }, './invariant': { default: './lib/invariant.js', types: './lib/types/invariant.d.ts' }, './cordis.patch.yml': './cordis.patch.yml', './package.json': './package.json', './startup': { default: './lib/startup.js', types: './lib/types/startup.d.ts' } }, bundlePatch: './cordis.patch.yml' },
    { name: '@dsh/desktop-plugin', observedVersion: '0.3.2', status: 'compatible', entrypoints: { '.': './lib/index.js', './client': './lib/client.js', './package.json': './package.json' }, bundlePatch: './cordis.patch.yml' },
    ...(['@deepseek-ai/dsh-llm-pi-ai', '@deepseek-ai/dsh-skill', '@deepseek-ai/dsh-mcp-client'] as const).map((name) => ({ name, observedVersion: '0.1.1-rc.2', status: 'compatible' as const, entrypoints: { '.': { default: './lib/index.js', types: './lib/types/index.d.ts' }, './invariant': { default: './lib/invariant.js', types: './lib/types/invariant.d.ts' }, './package.json': './package.json' } } as const)),
  ],
  capabilities: {
    apiProvider: { package: '@deepseek-ai/dsh-llm-pi-ai', available: true },
    skill: { package: '@deepseek-ai/dsh-skill', available: true },
    mcp: { package: '@deepseek-ai/dsh-mcp-client', available: true },
  },
  officialClosure: {
    digest: createHash('sha256').update(JSON.stringify(closurePackages)).digest('hex'),
    packages: closurePackages,
  },
  featureGroups: {
    modelProvider: { packages: ['@deepseek-ai/dsh-llm-pi-ai'], available: true },
    sessionTrajectory: { packages: [], available: false },
    planGoal: { packages: [], available: false },
    jobsScheduling: { packages: [], available: false },
    skill: { packages: ['@deepseek-ai/dsh-skill'], available: true },
    mcp: { packages: ['@deepseek-ai/dsh-mcp-client'], available: true },
    subagent: { packages: [], available: false },
    workflow: { packages: [], available: false },
    approvalQuestions: { packages: [], available: false },
    filesystemShell: { packages: [], available: false },
    webTools: { packages: [], available: false },
    hooksWebhooks: { packages: [], available: false },
    sessionsSettings: { packages: [], available: false },
    officialWebUi: { packages: ['@deepseek-ai/dsh-web-app'], available: true },
  },
}
const expectedVersions = { dshVersion: '0.1.1-rc.2', desktopPluginVersion: '0.3.2' }
const temporaryRoots: string[] = []
afterEach(() => { for (const root of temporaryRoots.splice(0)) rmSync(root, { recursive: true, force: true }) })

function fixture(bundles: string[]) {
  const root = mkdtempSync(join(tmpdir(), 'deepseek-harness-profile-'))
  temporaryRoots.push(root)
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
  it('does not mutate for a stale internally-consistent report when caller-owned versions differ', () => {
    const path = fixture([])
    expect(() => ensureDesktopProfile(path, compatibleReport, { ...expectedVersions, dshVersion: '0.1.1-rc.999' })).toThrow(/compatible capability record/i)
    expect(JSON.parse(readFileSync(path, 'utf8')).dsh.profile.bundles).toEqual([])
  })
  it('hard-disables browser handoff for the Desktop web runtime', () => {
    const patch = readFileSync(desktopPatchPath, 'utf8')

    expect(patch).toMatch(/- id: web-runtime[\s\S]*?config:[\s\S]*?openBrowser: false/)
  })

  it('inserts the official web app between base and Desktop plugin', () => {
    const path = fixture(['@deepseek-ai/dsh-base', '@dsh/desktop-plugin'])

    expect(ensureDesktopProfile(path, compatibleReport, expectedVersions)).toBe(true)
    expect(JSON.parse(readFileSync(path, 'utf8')).dsh.profile.bundles).toEqual(DESKTOP_BUNDLES)
  })

  it('repairs stale ordering and preserves unrelated manifest fields', () => {
    const path = fixture(['@dsh/desktop-plugin', '@deepseek-ai/dsh-base'])
    const before = JSON.parse(readFileSync(path, 'utf8'))

    ensureDesktopProfile(path, compatibleReport, expectedVersions)

    const after = JSON.parse(readFileSync(path, 'utf8'))
    expect(after.name).toBe(before.name)
    expect(after.dependencies).toEqual(before.dependencies)
    expect(after.dsh.profile.bundles).toEqual(DESKTOP_BUNDLES)
  })

  it('does not rewrite an already-correct profile', () => {
    const path = fixture([...DESKTOP_BUNDLES])

    expect(ensureDesktopProfile(path, compatibleReport, expectedVersions)).toBe(false)
  })

  it('refuses to mount a profile without the exact compatible capability report', () => {
    const path = fixture([])
    const invalidReport = (value: object) => value as Parameters<typeof ensureDesktopProfile>[1]

    expect(() => ensureDesktopProfile(path, invalidReport({ schemaVersion: 1 }), expectedVersions)).toThrow(/capabilit/i)
    expect(() => ensureDesktopProfile(path, invalidReport({ schemaVersion: 1, profileBundles: ['@deepseek-ai/dsh-base'] }), expectedVersions)).toThrow(/capabilit/i)
  })

  it('refuses an exact bundle list without successful package capability records', () => {
    const path = fixture([])
    const invalidReport = (value: object) => value as Parameters<typeof ensureDesktopProfile>[1]

    expect(() => ensureDesktopProfile(path, invalidReport({ profileBundles: [...DESKTOP_BUNDLES] }), expectedVersions)).toThrow(/capabilit/i)
  })

  it.each([
    ['a missing schema version', (report: typeof compatibleReport) => { delete (report as { schemaVersion?: number }).schemaVersion }],
    ['a malformed required observed version', (report: typeof compatibleReport) => { report.packages[0].observedVersion = null as never }],
    ['a tampered required entrypoint', (report: typeof compatibleReport) => { ((report.packages[1].entrypoints as Record<string, { default: string }>)['.']).default = './lib/tampered.js' }],
    ['a duplicate package record', (report: typeof compatibleReport) => { report.packages.push({ ...report.packages[0] }) }],
  ])('rejects %s before mutating the desktop profile', (_label, mutate) => {
    const path = fixture([])
    const report = JSON.parse(JSON.stringify(compatibleReport)) as typeof compatibleReport
    mutate(report)

    expect(() => ensureDesktopProfile(path, report, expectedVersions)).toThrow(/capabilit/i)
    expect(JSON.parse(readFileSync(path, 'utf8')).dsh.profile.bundles).toEqual([])
  })

  it.each([
    ['an unknown top-level timestamp', (report: Record<string, unknown>) => { report.timestamp = '2026-08-24T00:00:00Z' }],
    ['a package filesystem path', (report: Record<string, unknown>) => { ((report.packages as Array<Record<string, unknown>>)[0]).path = '/private/runtime' }],
    ['a package secret-shaped field', (report: Record<string, unknown>) => { ((report.packages as Array<Record<string, unknown>>)[0]).apiKey = 'must-not-pass' }],
    ['an unknown capabilities field', (report: Record<string, unknown>) => { (report.capabilities as Record<string, unknown>).extra = { package: '@spoofed/package', available: false } }],
    ['an unknown nested capability field', (report: Record<string, unknown>) => { ((report.capabilities as Record<string, Record<string, unknown>>).apiProvider).extra = true }],
  ])('rejects %s before any profile mutation', (_label, mutate) => {
    const path = fixture([])
    const report = JSON.parse(JSON.stringify(compatibleReport)) as Record<string, unknown>
    mutate(report)

    expect(() => ensureDesktopProfile(path, report as unknown as RuntimeCapabilityReport, expectedVersions)).toThrow(/capabilit/i)
    expect(JSON.parse(readFileSync(path, 'utf8')).dsh.profile.bundles).toEqual([])
  })
})
