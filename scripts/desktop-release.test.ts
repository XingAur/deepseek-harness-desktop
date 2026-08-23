import { createHash } from 'node:crypto'
import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { basename, join } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  generateDesktopRelease,
  productionRepository,
  verifyDesktopReleaseAssets,
} from './desktop-release.mjs'

const versions = {
  schemaVersion: 1 as const,
  desktopVersion: '0.1.13',
  runtimeVersion: '0.1.10-preview',
  dshVersion: '0.1.1-rc.2',
  nodeVersion: '24.14.0',
  pnpmVersion: '11.7.0',
  legacyReleaseBaseline: '0.1.12',
}

describe('desktop release metadata', () => {
  it('generates deterministic Windows in-app and macOS manual metadata', async () => {
    const fixture = await assetFixture()
    const publishedAt = '2026-08-23T08:30:00.000Z'
    const result = generateDesktopRelease({
      assetDirectory: fixture.assets,
      outputDirectory: fixture.output,
      repository: productionRepository,
      publishedAt,
      notes: 'Automated upstream release.',
      versions,
    })

    const latest = JSON.parse(await readFile(join(fixture.output, 'latest.json'), 'utf8'))
    const manifest = JSON.parse(await readFile(join(fixture.output, 'desktop-release.json'), 'utf8'))
    const baseUrl = 'https://github.com/XingAur/deepseek-harness-desktop/releases/download/desktop-v0.1.13/'

    expect(latest).toEqual({
      version: '0.1.13',
      notes: 'Automated upstream release.',
      pub_date: publishedAt,
      platforms: {
        'windows-x86_64': {
          signature: 'SIGNATURE',
          url: `${baseUrl}DeepSeek.Harness.Desktop_0.1.13_x64-setup.exe`,
        },
      },
    })
    expect(manifest).toMatchObject({
      schemaVersion: 1,
      version: '0.1.13',
      tag: 'desktop-v0.1.13',
      publishedAt,
      notes: 'Automated upstream release.',
      releasePageUrl: 'https://github.com/XingAur/deepseek-harness-desktop/releases/tag/desktop-v0.1.13',
      platforms: {
        'windows-x86_64': {
          mode: 'in-app',
          url: `${baseUrl}DeepSeek.Harness.Desktop_0.1.13_x64-setup.exe`,
          signatureUrl: `${baseUrl}DeepSeek.Harness.Desktop_0.1.13_x64-setup.exe.sig`,
          sha256: sha256('updater'),
          size: 7,
        },
        'darwin-aarch64': {
          mode: 'manual-dmg',
          url: `${baseUrl}DeepSeek.Harness.Desktop_0.1.13_aarch64.dmg`,
          sha256: sha256('dmg'),
          size: 3,
          developerIdSigned: false,
          notarized: false,
        },
      },
    })
    expect(result.uploadableAssets.map((path) => basename(path)).sort()).toEqual([
      'DeepSeek.Harness.Desktop_0.1.13_aarch64.dmg',
      'DeepSeek.Harness.Desktop_0.1.13_x64-setup.exe',
      'DeepSeek.Harness.Desktop_0.1.13_x64-setup.exe.sig',
      'desktop-release.json',
      'latest.json',
    ])
    expect(await readFile(join(fixture.output, 'latest.json'), 'utf8')).toMatch(/\n$/)
    expect(await readFile(join(fixture.output, 'desktop-release.json'), 'utf8')).toMatch(/\n$/)
  })

  it('rejects artifact names that GitHub would normalize during upload', async () => {
    const fixture = await assetFixture('DeepSeek Harness Desktop')
    expect(() => verifyDesktopReleaseAssets({ assetDirectory: fixture.assets, versions })).toThrow(/不安全的发布资产/)
  })

  it('does not allow repository substitution', async () => {
    const fixture = await assetFixture()
    expect(() => generateDesktopRelease({
      assetDirectory: fixture.assets,
      outputDirectory: fixture.output,
      repository: 'attacker/example',
      publishedAt: '2026-08-23T08:30:00.000Z',
      notes: '',
      versions,
    })).toThrow(/固定仓库/)
  })

  it.each([
    ['missing signature', async (assets: string) => writeFile(join(assets, 'DeepSeek.Harness.Desktop_0.1.13_x64-setup.exe.sig'), '')],
    ['duplicate DMG', async (assets: string) => writeFile(join(assets, 'Another_0.1.13_aarch64.dmg'), 'dmg')],
    ['wrong version', async (assets: string) => writeFile(join(assets, 'DeepSeek.Harness.Desktop_0.1.12_x64-setup.exe'), 'old')],
    ['unexpected platform', async (assets: string) => writeFile(join(assets, 'DeepSeek.Harness.Desktop_0.1.13_x64.dmg'), 'intel')],
    ['traversal-like name', async (assets: string) => writeFile(join(assets, 'DeepSeek..Harness_0.1.13_x64-setup.exe'), 'bad')],
    ['legacy updater format', async (assets: string) => writeFile(join(assets, 'DeepSeek.Harness.Desktop_0.1.13_x64-setup.nsis.zip'), 'old')],
  ])('rejects %s assets', async (_label, mutate) => {
    const fixture = await assetFixture()
    await mutate(fixture.assets)
    expect(() => verifyDesktopReleaseAssets({ assetDirectory: fixture.assets, versions })).toThrow()
  })
})

async function assetFixture(prefix = 'DeepSeek.Harness.Desktop') {
  const root = await mkdtemp(join(tmpdir(), 'desktop-release-'))
  const assets = join(root, 'assets')
  const output = join(root, 'output')
  await mkdir(assets)
  await mkdir(output)
  await writeFile(join(assets, `${prefix}_0.1.13_x64-setup.exe`), 'updater')
  await writeFile(join(assets, `${prefix}_0.1.13_x64-setup.exe.sig`), 'SIGNATURE\n')
  await writeFile(join(assets, `${prefix}_0.1.13_aarch64.dmg`), 'dmg')
  return { assets, output }
}

function sha256(value: string) {
  return createHash('sha256').update(value).digest('hex')
}
