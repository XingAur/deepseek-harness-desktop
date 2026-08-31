import { createHash } from 'node:crypto'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { compareManifest, loadPublishedManifest, resolveExpectedSha, sha256File } from './check-runtime-plugin-currency.mjs'

const expectedSha = createHash('sha256').update('desktop-plugin').digest('hex')
const manifestAssetArgs = ['repos/o/r/releases/assets/42']

function ghApiStub(releases, assetBody) {
  const calls = []
  return {
    calls,
    ghApi(args) {
      calls.push(args)
      if (args[0] === 'repos/o/r/releases?per_page=100') return { status: 'ok', value: JSON.stringify(releases) }
      if (args[1] === 'Accept: application/octet-stream' && args[2] === manifestAssetArgs[0]) {
        return { status: 'ok', value: assetBody }
      }
      throw new Error(`意外的 gh api 调用: ${args.join(' ')}`)
    },
  }
}

describe('compareManifest', () => {
  it('rejects manifests published before the desktop plugin fingerprint existed', () => {
    expect(compareManifest(expectedSha, { schemaVersion: 1, sha256: 'a'.repeat(64) })).toEqual({
      ok: false,
      reason: 'stale-manifest',
    })
    expect(compareManifest(expectedSha, { desktopPluginSha256: '' })).toEqual({
      ok: false,
      reason: 'stale-manifest',
    })
    expect(compareManifest(expectedSha, null)).toEqual({ ok: false, reason: 'stale-manifest' })
  })

  it('reports plugin drift with the expected and actual fingerprints', () => {
    const actual = createHash('sha256').update('stale-plugin').digest('hex')
    expect(compareManifest(expectedSha, { desktopPluginSha256: actual })).toEqual({
      ok: false,
      reason: 'plugin-drift',
      expected: expectedSha,
      actual,
    })
  })

  it('accepts a manifest whose fingerprint matches the current plugin', () => {
    expect(compareManifest(expectedSha.toUpperCase(), { desktopPluginSha256: expectedSha })).toEqual({ ok: true })
  })
})

describe('resolveExpectedSha', () => {
  it('hashes the plugin tarball produced in the injected pack destination without network access', () => {
    const repositoryRoot = mkdtempSync(join(tmpdir(), 'dsh-plugin-currency-root-'))
    const calls = []
    const tarballBytes = 'fake-plugin-tarball'
    const runNpm = (cwd, args) => {
      calls.push({ cwd, args })
      if (args[0] === 'pack') {
        const packDestination = args[args.indexOf('--pack-destination') + 1]
        writeFileSync(join(packDestination, 'dsh-desktop-plugin-0.3.2.tgz'), tarballBytes)
      }
    }

    expect(resolveExpectedSha(repositoryRoot, runNpm)).toBe(
      createHash('sha256').update(tarballBytes).digest('hex'),
    )
    expect(calls.map((call) => call.args[1])).toEqual(['plugin:build', './packages/dsh-plugin-desktop'])
    expect(calls.every((call) => call.cwd === repositoryRoot)).toBe(true)
  })
})

describe('loadPublishedManifest', () => {
  const repository = 'o/r'
  const runtimeVersion = '0.1.17-preview'
  const windowsRelease = {
    tag_name: 'runtime-v0.1.17-preview',
    assets: [{ name: 'runtime-windows-x86_64.json', id: 42 }],
  }

  it('treats an absent runtime release as missing', async () => {
    const stub = ghApiStub([], '')
    await expect(loadPublishedManifest({ repository, runtimeVersion, target: 'windows-x86_64' }, stub.ghApi))
      .resolves.toEqual({ status: 'missing' })
  })

  it('treats a release without the target manifest asset as missing', async () => {
    const stub = ghApiStub([{ tag_name: 'runtime-v0.1.17-preview', assets: [{ name: 'runtime-darwin-aarch64.json', id: 7 }] }], '')
    await expect(loadPublishedManifest({ repository, runtimeVersion, target: 'windows-x86_64' }, stub.ghApi))
      .resolves.toEqual({ status: 'missing' })
  })

  it('downloads the manifest asset through the octet-stream API and parses it', async () => {
    const manifest = { schemaVersion: 1, desktopPluginSha256: expectedSha }
    const stub = ghApiStub([windowsRelease], JSON.stringify(manifest))
    await expect(loadPublishedManifest({ repository, runtimeVersion, target: 'windows-x86_64' }, stub.ghApi))
      .resolves.toEqual({ status: 'found', manifest })
    expect(stub.calls.at(-1)).toEqual(['-H', 'Accept: application/octet-stream', ...manifestAssetArgs])
  })

  it('rejects malformed manifest assets and malformed release lists', async () => {
    const brokenAsset = ghApiStub([windowsRelease], 'not-json')
    await expect(loadPublishedManifest({ repository, runtimeVersion, target: 'windows-x86_64' }, brokenAsset.ghApi))
      .rejects.toThrow(/不是有效的 JSON 清单/)
    const brokenList = ghApiStub('nope', '')
    await expect(loadPublishedManifest({ repository, runtimeVersion, target: 'windows-x86_64' }, brokenList.ghApi))
      .rejects.toThrow(/release 列表格式无效/)
  })

  it('falls back to the public release download URL when gh is unavailable', async () => {
    const originalFetch = globalThis.fetch
    const fetchedUrls = []
    globalThis.fetch = (async (url) => {
      fetchedUrls.push(String(url))
      return new Response(JSON.stringify({ desktopPluginSha256: expectedSha }), { status: 200 })
    }) as typeof fetch
    try {
      await expect(loadPublishedManifest({ repository, runtimeVersion, target: 'windows-x86_64' }, () => ({ status: 'gh-missing' })))
        .resolves.toEqual({ status: 'found', manifest: { desktopPluginSha256: expectedSha } })
      expect(fetchedUrls).toEqual([
        'https://github.com/o/r/releases/download/runtime-v0.1.17-preview/runtime-windows-x86_64.json',
      ])
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('treats a public download 404 as missing', async () => {
    const originalFetch = globalThis.fetch
    globalThis.fetch = (async () => new Response('Not Found', { status: 404 })) as typeof fetch
    try {
      await expect(loadPublishedManifest({ repository, runtimeVersion, target: 'windows-x86_64' }, () => ({ status: 'gh-missing' })))
        .resolves.toEqual({ status: 'missing' })
    } finally {
      globalThis.fetch = originalFetch
    }
  })
})

describe('sha256File', () => {
  it('matches the build-runtime tarball hashing algorithm', () => {
    const directory = mkdtempSync(join(tmpdir(), 'dsh-plugin-currency-sha-'))
    const file = join(directory, 'plugin.tgz')
    writeFileSync(file, 'tarball-bytes')
    expect(sha256File(file)).toBe(createHash('sha256').update('tarball-bytes').digest('hex'))
  })
})
