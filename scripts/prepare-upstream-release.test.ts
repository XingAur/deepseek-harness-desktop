import { mkdtemp, mkdir, readFile, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it, vi } from 'vitest'
import { assertReleaseVersionConsistency } from './release-versions.mjs'
import {
  compareSemVer,
  fetchLatestDshSource,
  fetchLatestDshVersion,
  parseDshTagRefs,
  prepareUpstreamRelease,
} from './prepare-upstream-release.mjs'

const initialSource = {
  repository: 'https://github.com/deepseek-ai/deepseek-harness.git',
  tag: 'dsh-v0.1.0-rc.8',
  commit: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
}
const latestSource = {
  repository: 'https://github.com/deepseek-ai/deepseek-harness.git',
  tag: 'dsh-v0.1.2-alpha.1',
  commit: 'cd5ef8148158c3a752a658978873241fdf8e2bbc',
}

const trackedFiles = [
  'release/versions.json',
  'package.json',
  'package-lock.json',
  'src-tauri/tauri.conf.json',
  'src-tauri/Cargo.toml',
  'src-tauri/Cargo.lock',
]

describe('prepare upstream release', () => {
  it('bumps desktop and Runtime patch versions for a newer DSH version', async () => {
    const root = await releaseFixture()

    const result = await prepareUpstreamRelease({ root, latestVersion: '0.1.1-rc.2', latestSource })

    expect(result).toEqual({
      action: 'upgrade',
      previousDshVersion: '0.1.0-rc.8',
      dshVersion: '0.1.1-rc.2',
      desktopVersion: '0.1.13',
      runtimeVersion: '0.1.10-preview',
      tag: 'desktop-v0.1.13',
      previousUpstreamTag: 'dsh-v0.1.0-rc.8',
      upstreamTag: 'dsh-v0.1.2-alpha.1',
      upstreamCommit: 'cd5ef8148158c3a752a658978873241fdf8e2bbc',
    })
    expect(assertReleaseVersionConsistency(root)).toMatchObject({
      desktopVersion: '0.1.13',
      runtimeVersion: '0.1.10-preview',
      dshVersion: '0.1.1-rc.2',
      dshUpstream: latestSource,
    })
  })

  it('tracks a newer official source tag without pretending it is an npm release', async () => {
    const root = await releaseFixture()

    const result = await prepareUpstreamRelease({ root, latestVersion: '0.1.0-rc.8', latestSource })

    expect(result).toMatchObject({
      action: 'source-update',
      previousDshVersion: '0.1.0-rc.8',
      dshVersion: '0.1.0-rc.8',
      desktopVersion: '0.1.12',
      runtimeVersion: '0.1.9-preview',
      previousUpstreamTag: 'dsh-v0.1.0-rc.8',
      upstreamTag: 'dsh-v0.1.2-alpha.1',
      upstreamCommit: 'cd5ef8148158c3a752a658978873241fdf8e2bbc',
    })
    expect(assertReleaseVersionConsistency(root)).toMatchObject({
      desktopVersion: '0.1.12',
      runtimeVersion: '0.1.9-preview',
      dshVersion: '0.1.0-rc.8',
      dshUpstream: latestSource,
    })
  })

  it('is a byte-preserving no-op when the version is unchanged', async () => {
    const root = await releaseFixture()
    const before = await snapshotFixture(root)

    expect(await prepareUpstreamRelease({ root, latestVersion: '0.1.0-rc.8', latestSource: initialSource })).toEqual({
      action: 'noop',
      previousDshVersion: '0.1.0-rc.8',
      dshVersion: '0.1.0-rc.8',
      desktopVersion: '0.1.12',
      runtimeVersion: '0.1.9-preview',
      tag: 'desktop-v0.1.12',
      previousUpstreamTag: 'dsh-v0.1.0-rc.8',
      upstreamTag: 'dsh-v0.1.0-rc.8',
      upstreamCommit: 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    })
    expect(await snapshotFixture(root)).toEqual(before)
  })

  it.each(['0.1.0-rc.7', '0.0.9', 'not-a-version', '^0.2.0', 'latest'])(
    'rejects unsafe upstream version %s without changing files',
    async (version) => {
      const root = await releaseFixture()
      const before = await snapshotFixture(root)
      await expect(prepareUpstreamRelease({ root, latestVersion: version, latestSource: initialSource })).rejects.toThrow()
      expect(await snapshotFixture(root)).toEqual(before)
    },
  )

  it('compares stable and prerelease SemVer identifiers correctly', () => {
    expect(compareSemVer('1.0.0-rc.2', '1.0.0-rc.10')).toBeLessThan(0)
    expect(compareSemVer('1.0.0-rc.10', '1.0.0')).toBeLessThan(0)
    expect(compareSemVer('1.0.0', '1.0.0')).toBe(0)
    expect(compareSemVer('2.0.0', '1.99.99')).toBeGreaterThan(0)
    expect(compareSemVer('1.0.0-9007199254740992', '1.0.0-9007199254740993')).toBeLessThan(0)
  })

  it('fetches only the fixed npm latest endpoint without redirects', async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ version: '0.1.1-rc.2' }), {
      status: 200,
      headers: { 'content-type': 'application/json' },
    }))

    await expect(fetchLatestDshVersion(fetcher)).resolves.toBe('0.1.1-rc.2')
    expect(fetcher).toHaveBeenCalledOnce()
    expect(fetcher).toHaveBeenCalledWith(
      'https://registry.npmjs.org/@deepseek-ai%2Fdsh/latest',
      expect.objectContaining({ redirect: 'error', signal: expect.any(AbortSignal) }),
    )
  })

  it('parses lightweight and annotated official tags using SemVer order', () => {
    expect(parseDshTagRefs([
      'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\trefs/tags/dsh-v0.1.1-rc.10',
      'cccccccccccccccccccccccccccccccccccccccc\trefs/tags/dsh-v0.1.2-alpha.1',
      'cd5ef8148158c3a752a658978873241fdf8e2bbc\trefs/tags/dsh-v0.1.2-alpha.1^{}',
      'dddddddddddddddddddddddddddddddddddddddd\trefs/tags/not-dsh',
    ].join('\n'))).toEqual({
      repository: 'https://github.com/deepseek-ai/deepseek-harness.git',
      tag: 'dsh-v0.1.2-alpha.1',
      version: '0.1.2-alpha.1',
      commit: 'cd5ef8148158c3a752a658978873241fdf8e2bbc',
    })
  })

  it('runs a bounded read against the fixed public repository', () => {
    const runner = vi.fn(() => ({
      status: 0,
      stdout: 'cd5ef8148158c3a752a658978873241fdf8e2bbc\trefs/tags/dsh-v0.1.2-alpha.1\n',
    }))

    expect(fetchLatestDshSource(runner)).toMatchObject(latestSource)
    expect(runner).toHaveBeenCalledWith('git', [
      'ls-remote', '--tags',
      'https://github.com/deepseek-ai/deepseek-harness.git',
      'refs/tags/dsh-v*',
    ], expect.objectContaining({ encoding: 'utf8', timeout: 10_000 }))
  })

  it.each(['', 'not-a-ref', 'abc\trefs/tags/dsh-v0.1.2-alpha.1'])('rejects invalid source refs without guessing: %s', (refs) => {
    expect(() => parseDshTagRefs(refs)).toThrow(/官方 DSH tag/)
  })

  it.each([
    new Response('offline', { status: 503 }),
    new Response('{', { status: 200 }),
    new Response(JSON.stringify({ version: 'latest' }), { status: 200 }),
  ])('rejects invalid npm responses', async (response) => {
    await expect(fetchLatestDshVersion(async () => response)).rejects.toThrow()
  })
})

async function releaseFixture() {
  const root = await mkdtemp(join(tmpdir(), 'dsh-release-versions-'))
  for (const directory of ['release', 'src-tauri', 'scripts']) await mkdir(join(root, directory), { recursive: true })
  await writeJson(root, 'release/versions.json', {
    schemaVersion: 2,
    desktopVersion: '0.1.12',
    runtimeVersion: '0.1.9-preview',
    dshVersion: '0.1.0-rc.8',
    dshUpstream: initialSource,
    nodeVersion: '24.14.0',
    pnpmVersion: '11.7.0',
    legacyReleaseBaseline: '0.1.12',
  })
  await writeJson(root, 'package.json', { name: 'fixture', version: '0.1.12' })
  await writeJson(root, 'package-lock.json', {
    name: 'fixture', version: '0.1.12', lockfileVersion: 3,
    packages: { '': { name: 'fixture', version: '0.1.12' } },
  })
  await writeJson(root, 'src-tauri/tauri.conf.json', { version: '0.1.12' })
  await writeFile(join(root, 'src-tauri/Cargo.toml'), '[package]\nname = "deepseek-harness-desktop"\nversion = "0.1.12"\n')
  await writeFile(join(root, 'src-tauri/Cargo.lock'), '[[package]]\nname = "deepseek-harness-desktop"\nversion = "0.1.12"\n')
  await writeFile(join(root, 'scripts/build-runtime.mjs'), [
    "import { loadReleaseVersions } from './release-versions.mjs'",
    'const versions = loadReleaseVersions()',
    'versions.nodeVersion', 'versions.dshVersion', 'versions.pnpmVersion',
  ].join('\n'))
  await writeFile(join(root, 'scripts/windows-installer.mjs'), [
    "import { loadReleaseVersions } from './release-versions.mjs'",
    'loadReleaseVersions().runtimeVersion',
  ].join('\n'))
  return root
}

async function writeJson(root: string, path: string, value: unknown) {
  await writeFile(join(root, path), `${JSON.stringify(value, null, 2)}\n`)
}

async function snapshotFixture(root: string) {
  return Object.fromEntries(await Promise.all(trackedFiles.map(async (path) => [path, await readFile(join(root, path), 'utf8')])))
}
