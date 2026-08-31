import { createHash } from 'node:crypto'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { compareManifest, currentPluginSha256, fetchRuntimeManifest, sha256File } from './check-runtime-plugin-currency.mjs'

const expectedSha = createHash('sha256').update('desktop-plugin').digest('hex')
const runtimeTag = 'runtime-v0.1.16-preview'

describe('compareManifest', () => {
  it('accepts a manifest whose fingerprint matches the current plugin', () => {
    expect(compareManifest(expectedSha, { desktopPluginSha256: expectedSha }, runtimeTag)).toEqual({ ok: true })
    // 指纹比对忽略大小写。
    expect(compareManifest(expectedSha.toUpperCase(), { desktopPluginSha256: expectedSha }, runtimeTag)).toEqual({ ok: true })
  })

  it('rejects manifests published before the desktop plugin fingerprint existed', () => {
    const stale = {
      ok: false,
      reason: 'runtime-manifest-stale',
      message: `运行时 ${runtimeTag} 的 manifest 缺少插件 sha(运行时过旧),必须 bump runtimeVersion 重新发布`,
    }
    expect(compareManifest(expectedSha, { schemaVersion: 1, sha256: 'a'.repeat(64) }, runtimeTag)).toEqual(stale)
    expect(compareManifest(expectedSha, { desktopPluginSha256: '' }, runtimeTag)).toEqual(stale)
    expect(compareManifest(expectedSha, null, runtimeTag)).toEqual(stale)
  })

  it('reports plugin drift with a message containing both fingerprints', () => {
    const stalePluginSha = createHash('sha256').update('stale-plugin').digest('hex')
    expect(compareManifest(expectedSha, { desktopPluginSha256: stalePluginSha }, runtimeTag)).toEqual({
      ok: false,
      reason: 'plugin-drift',
      message: `运行时 ${runtimeTag} 内插件与当前仓库不一致:manifest=${stalePluginSha} 当前=${expectedSha},必须 bump runtimeVersion`,
    })
  })
})

describe('fetchRuntimeManifest', () => {
  const target = 'windows-x86_64'
  const repository = 'acme/deepseek-harness'
  const manifestName = `runtime-${target}.json`
  const archiveName = 'dsh-runtime-windows-x86_64.zip'
  const assetId = 42
  const assetUrl = `https://api.github.com/repos/${repository}/releases/assets/${assetId}`
  const untrustedAssetUrl = 'https://attacker.invalid/runtime-manifest.json'
  const releaseListUrl = (page = 1) => `https://api.github.com/repos/${repository}/releases?per_page=100&page=${page}`
  const draftRelease = (assets) => ({ tag_name: runtimeTag, draft: true, assets })
  const archiveAsset = (overrides = {}) => ({ id: assetId + 1, name: archiveName, url: untrustedAssetUrl, ...overrides })
  const manifestAsset = (overrides = {}) => ({ id: assetId, name: manifestName, url: untrustedAssetUrl, ...overrides })
  const testOptions = (fetchImpl, overrides = {}) => ({
    repository,
    fetchImpl,
    retryDelayMs: 0,
    sleepImpl: async () => {},
    ...overrides,
  })

  it.each([
    ['rejects a draft manifest without a desktop plugin fingerprint', { schemaVersion: 1 }, 'runtime-manifest-stale'],
    ['rejects a draft manifest with a stale desktop plugin fingerprint', { desktopPluginSha256: 'f'.repeat(64) }, 'plugin-drift'],
    ['accepts a draft manifest with the current desktop plugin fingerprint', { desktopPluginSha256: expectedSha }, undefined],
  ])('%s for a complete draft Runtime Release', async (_description, draftManifest, reason) => {
    const calls: string[] = []
    const fetchImpl = async (url: string) => {
      calls.push(url)
      if (url.includes('/releases/download/')) return { status: 404, ok: false }
      if (url === releaseListUrl()) {
        return {
          status: 200,
          ok: true,
          text: async () => JSON.stringify([draftRelease([archiveAsset(), manifestAsset()])]),
        }
      }
      if (url === assetUrl) return { status: 200, ok: true, text: async () => JSON.stringify(draftManifest) }
      throw new Error(`unexpected URL: ${url}`)
    }

    const manifest = await fetchRuntimeManifest(target, runtimeTag, 'test-token', testOptions(fetchImpl))

    expect(manifest).toEqual(draftManifest)
    expect(compareManifest(expectedSha, manifest, runtimeTag).reason).toBe(reason)
    expect(calls).toEqual([
      releaseListUrl(),
      assetUrl,
    ])
  })

  it.each([
    ['the Runtime Release tag does not exist', [{ tag_name: `${runtimeTag}-different`, assets: [] }]],
    ['the Runtime Release has neither target archive nor manifest asset', [draftRelease([])]],
  ])('allows a new Runtime build when %s', async (_description, releases) => {
    const fetchImpl = async (url: string) => {
      if (url.includes('/releases/download/')) return { status: 404, ok: false }
      if (url === releaseListUrl()) return { status: 200, ok: true, text: async () => JSON.stringify(releases) }
      throw new Error(`unexpected URL: ${url}`)
    }

    await expect(fetchRuntimeManifest(target, runtimeTag, 'test-token', testOptions(fetchImpl))).resolves.toBeNull()
  })

  it.each([
    ['the target archive exists but the manifest is missing', [archiveAsset()], /archive.*manifest.*bump runtimeVersion|清理旧 draft asset/],
    ['the target manifest exists but the archive is missing', [manifestAsset()], /manifest.*archive/],
  ])('rejects a partial Runtime Release when %s', async (_description, assets, expectedError) => {
    const fetchImpl = async (url: string) => {
      if (url.includes('/releases/download/')) return { status: 404, ok: false }
      if (url === releaseListUrl()) return { status: 200, ok: true, text: async () => JSON.stringify([draftRelease(assets)]) }
      if (url === assetUrl) return { status: 200, ok: true, text: async () => JSON.stringify({ desktopPluginSha256: expectedSha }) }
      throw new Error(`unexpected URL: ${url}`)
    }

    await expect(fetchRuntimeManifest(target, runtimeTag, 'test-token', testOptions(fetchImpl))).rejects.toThrow(expectedError)
  })

  it('searches later Release list pages for the exact Runtime tag', async () => {
    const firstPage = Array.from({ length: 100 }, (_, index) => ({ tag_name: `runtime-vother-${index}`, assets: [] }))
    const fetchImpl = async (url: string) => {
      if (url.includes('/releases/download/')) return { status: 404, ok: false }
      if (url === releaseListUrl(1)) return { status: 200, ok: true, text: async () => JSON.stringify(firstPage) }
      if (url === releaseListUrl(2)) {
        return {
          status: 200,
          ok: true,
          text: async () => JSON.stringify([draftRelease([archiveAsset(), manifestAsset()])]),
        }
      }
      if (url === assetUrl) return { status: 200, ok: true, text: async () => JSON.stringify({ desktopPluginSha256: expectedSha }) }
      throw new Error(`unexpected URL: ${url}`)
    }

    await expect(fetchRuntimeManifest(target, runtimeTag, 'test-token', testOptions(fetchImpl))).resolves.toEqual({
      desktopPluginSha256: expectedSha,
    })
  })

  it.each([
    ['on the same page', () => [draftRelease([archiveAsset(), manifestAsset()]), draftRelease([archiveAsset(), manifestAsset({ id: assetId + 2 })])]],
    ['across pages', () => Array.from({ length: 100 }, (_, index) => (
      index === 0 ? draftRelease([archiveAsset(), manifestAsset()]) : { tag_name: `runtime-vother-${index}`, assets: [] }
    ))],
  ])('rejects duplicate exact Runtime Release tags %s', async (_description, firstPageFactory) => {
    const firstPage = firstPageFactory()
    const fetchImpl = async (url: string) => {
      if (url === releaseListUrl(1)) return { status: 200, ok: true, text: async () => JSON.stringify(firstPage) }
      if (url === releaseListUrl(2)) {
        return { status: 200, ok: true, text: async () => JSON.stringify([draftRelease([archiveAsset(), manifestAsset({ id: assetId + 2 })])]) }
      }
      throw new Error(`unexpected URL: ${url}`)
    }

    await expect(fetchRuntimeManifest(target, runtimeTag, 'test-token', testOptions(fetchImpl))).rejects.toThrow(/多个 Runtime Release 使用相同 tag/)
  })

  it.each([
    ['fails when the Release list request has a network error', releaseListUrl(), new Error('connection reset'), /查询 .* connection reset/],
    ['fails when the Release list request keeps returning 5xx', releaseListUrl(), { status: 503, ok: false }, /查询 .* HTTP 503/],
    ['fails when the Release list request is rejected', releaseListUrl(), { status: 401, ok: false }, /查询 .* HTTP 401/],
    ['fails when the Release list request is forbidden', releaseListUrl(), { status: 403, ok: false }, /查询 .* HTTP 403/],
    ['fails when the Release list endpoint is not found', releaseListUrl(), { status: 404, ok: false }, /查询 .* HTTP 404/],
    ['fails when the manifest asset returns 404', assetUrl, { status: 404, ok: false }, /下载 .* HTTP 404/],
    ['fails when the manifest asset keeps returning 5xx', assetUrl, { status: 503, ok: false }, /下载 .* HTTP 503/],
    ['fails when the Release list response is not an array', releaseListUrl(), {
      status: 200,
      ok: true,
      text: async () => JSON.stringify({ draft: true }),
    }, /Release 列表不是 JSON 数组/],
    ['fails when the manifest asset is invalid JSON', assetUrl, {
      status: 200,
      ok: true,
      text: async () => '{not-json',
    }, /不是有效的 JSON 清单/],
  ])('%s', async (_description, failureUrl, failureResponse, expectedError) => {
    const fetchImpl = async (url: string) => {
      if (url.includes('/releases/download/')) return { status: 404, ok: false }
      if (url === failureUrl) {
        if (failureResponse instanceof Error) throw failureResponse
        return failureResponse
      }
      if (url === releaseListUrl()) {
        return {
          status: 200,
          ok: true,
          text: async () => JSON.stringify([draftRelease([archiveAsset(), manifestAsset()])]),
        }
      }
      throw new Error(`unexpected URL: ${url}`)
    }

    await expect(fetchRuntimeManifest(target, runtimeTag, 'test-token', testOptions(fetchImpl))).rejects.toThrow(expectedError)
  })

  it('downloads a complete Release manifest from its verified asset id', async () => {
    const calls: string[] = []
    const manifest = { desktopPluginSha256: expectedSha }
    const fetchImpl = async (url: string) => {
      calls.push(url)
      if (url === releaseListUrl()) {
        return { status: 200, ok: true, text: async () => JSON.stringify([draftRelease([archiveAsset(), manifestAsset()])]) }
      }
      if (url === assetUrl) return { status: 200, ok: true, text: async () => JSON.stringify(manifest) }
      throw new Error(`unexpected URL: ${url}`)
    }

    await expect(fetchRuntimeManifest(target, runtimeTag, 'test-token', testOptions(fetchImpl))).resolves.toEqual(manifest)
    expect(calls).toEqual([
      releaseListUrl(),
      assetUrl,
    ])
  })

  it('rejects a manifest-only Release before any manifest download', async () => {
    const calls: string[] = []
    const fetchImpl = async (url: string) => {
      calls.push(url)
      if (url === releaseListUrl()) {
        return { status: 200, ok: true, text: async () => JSON.stringify([draftRelease([manifestAsset()])]) }
      }
      throw new Error(`unexpected URL: ${url}`)
    }

    await expect(fetchRuntimeManifest(target, runtimeTag, 'test-token', testOptions(fetchImpl))).rejects.toThrow(/manifest.*archive/)
    expect(calls).toEqual([releaseListUrl()])
  })

  it('uses the authenticated GitHub asset endpoint instead of the untrusted asset URL', async () => {
    const calls: Array<{ url: string, init: { headers?: Record<string, string> } }> = []
    const fetchImpl = async (url: string, init) => {
      calls.push({ url, init })
      if (url.includes('/releases/download/')) return { status: 404, ok: false }
      if (url === releaseListUrl()) {
        return { status: 200, ok: true, text: async () => JSON.stringify([draftRelease([archiveAsset(), manifestAsset()])]) }
      }
      if (url === assetUrl) return { status: 200, ok: true, text: async () => JSON.stringify({ desktopPluginSha256: expectedSha }) }
      throw new Error(`unexpected URL: ${url}`)
    }

    await expect(fetchRuntimeManifest(target, runtimeTag, 'test-token', testOptions(fetchImpl))).resolves.toEqual({
      desktopPluginSha256: expectedSha,
    })
    expect(calls.map((call) => call.url)).toEqual([
      releaseListUrl(),
      assetUrl,
    ])
    expect(calls[0].init.headers).toMatchObject({
      Authorization: 'Bearer test-token',
      Accept: 'application/vnd.github+json',
    })
    expect(calls[1].init.headers).toMatchObject({
      Authorization: 'Bearer test-token',
      Accept: 'application/octet-stream',
    })
  })

  it.each([
    ['repository contains an extra path separator', 'acme/deepseek-harness/extra', runtimeTag, /无效的 GitHub 仓库/],
    ['runtime tag is latest', repository, 'latest', /Runtime tag 必须是固定的 runtime-v<semver>/],
    ['runtime tag is main', repository, 'main', /Runtime tag 必须是固定的 runtime-v<semver>/],
    ['runtime tag is a URL', repository, 'https://github.com/acme/deepseek-harness', /Runtime tag 必须是固定的 runtime-v<semver>/],
    ['runtime tag has a misspelled prefix', repository, 'runtim-v0.1.16-preview', /Runtime tag 必须是固定的 runtime-v<semver>/],
    ['runtime tag contains a control character', repository, 'runtime-v0.1.16\npreview', /Runtime tag 必须是固定的 runtime-v<semver>/],
  ])('rejects invalid %s before issuing any request', async (_description, invalidRepository, invalidRuntimeTag, expectedError) => {
    let calls = 0
    const fetchImpl = async () => {
      calls += 1
      throw new Error('fetch must not run')
    }

    await expect(fetchRuntimeManifest(target, invalidRuntimeTag, 'test-token', testOptions(fetchImpl, {
      repository: invalidRepository,
    }))).rejects.toThrow(expectedError)
    expect(calls).toBe(0)
  })

  it('fails instead of scanning beyond the bounded Release page limit', async () => {
    const fullPage = Array.from({ length: 100 }, (_, index) => ({ tag_name: `runtime-vother-${index}`, assets: [] }))
    const fetchImpl = async (url: string) => {
      if (url.includes('/releases/download/')) return { status: 404, ok: false }
      if (url === releaseListUrl()) return { status: 200, ok: true, text: async () => JSON.stringify(fullPage) }
      throw new Error(`unexpected URL: ${url}`)
    }

    await expect(fetchRuntimeManifest(target, runtimeTag, 'test-token', testOptions(fetchImpl, {
      maxReleasePages: 1,
    }))).rejects.toThrow(/Release 列表超过最大页数/)
  })

  it('fails when a full Release list page repeats', async () => {
    const fullPage = Array.from({ length: 100 }, (_, index) => ({ tag_name: `runtime-vother-${index}`, assets: [] }))
    const fetchImpl = async (url: string) => {
      if (url.includes('/releases/download/')) return { status: 404, ok: false }
      if (url === releaseListUrl(1) || url === releaseListUrl(2)) {
        return { status: 200, ok: true, text: async () => JSON.stringify(fullPage) }
      }
      throw new Error(`unexpected URL: ${url}`)
    }

    await expect(fetchRuntimeManifest(target, runtimeTag, 'test-token', testOptions(fetchImpl))).rejects.toThrow(/Release 列表页面重复/)
  })

  it.each([
    ['assets is not an array', { tag_name: runtimeTag, draft: true, assets: {} }, /缺少 assets 数组/],
    ['the target manifest asset has an invalid id', draftRelease([archiveAsset(), manifestAsset({ id: '42' })]), /无效的 asset id/],
  ])('fails when %s', async (_description, release, expectedError) => {
    const fetchImpl = async (url: string) => {
      if (url.includes('/releases/download/')) return { status: 404, ok: false }
      if (url === releaseListUrl()) return { status: 200, ok: true, text: async () => JSON.stringify([release]) }
      throw new Error(`unexpected URL: ${url}`)
    }

    await expect(fetchRuntimeManifest(target, runtimeTag, 'test-token', testOptions(fetchImpl))).rejects.toThrow(expectedError)
  })

  it.each([
    ['network errors', async () => { throw new Error('connection reset') }],
    ['request timeouts', async (_url, init) => new Promise((_resolve, reject) => {
      init.signal.addEventListener('abort', () => reject(init.signal.reason), { once: true })
    })],
  ])('retries Release list %s exactly four times before failing', async (_description, listFailure) => {
    let attempts = 0
    const fetchImpl = async (url: string, init) => {
      if (url.includes('/releases/download/')) return { status: 404, ok: false }
      if (url === releaseListUrl()) {
        attempts += 1
        return listFailure(url, init)
      }
      throw new Error(`unexpected URL: ${url}`)
    }

    await expect(fetchRuntimeManifest(target, runtimeTag, 'test-token', testOptions(fetchImpl, {
      requestTimeoutMs: 1,
    }))).rejects.toThrow(/已重试 3 次/)
    expect(attempts).toBe(4)
  })

  it.each(['the Release list', 'the manifest asset'])(
    'retries %s body read errors exactly four times before failing',
    async (phase) => {
      let bodyAttempts = 0
      const failingResponse = {
        status: 200,
        ok: true,
        text: async () => {
          bodyAttempts += 1
          throw new Error('body stream failed')
        },
      }
      const fetchImpl = async (url: string) => {
        if (url === releaseListUrl()) {
          return phase === 'the Release list'
            ? failingResponse
            : { status: 200, ok: true, text: async () => JSON.stringify([draftRelease([archiveAsset(), manifestAsset()])]) }
        }
        if (url === assetUrl && phase === 'the manifest asset') return failingResponse
        throw new Error(`unexpected URL: ${url}`)
      }

      await expect(fetchRuntimeManifest(target, runtimeTag, 'test-token', testOptions(fetchImpl))).rejects.toThrow(/body stream failed/)
      expect(bodyAttempts).toBe(4)
    },
  )

  it.each(['the Release list', 'the manifest asset'])(
    'times out and retries a hanging %s body read',
    async (phase) => {
      let bodyAttempts = 0
      const hangingResponse = (signal) => ({
        status: 200,
        ok: true,
        text: async () => {
          bodyAttempts += 1
          return new Promise((_resolve, reject) => {
            signal.addEventListener('abort', () => reject(signal.reason), { once: true })
          })
        },
      })
      const fetchImpl = async (url: string, init) => {
        if (url === releaseListUrl()) {
          return phase === 'the Release list'
            ? hangingResponse(init.signal)
            : { status: 200, ok: true, text: async () => JSON.stringify([draftRelease([archiveAsset(), manifestAsset()])]) }
        }
        if (url === assetUrl && phase === 'the manifest asset') return hangingResponse(init.signal)
        throw new Error(`unexpected URL: ${url}`)
      }
      let deadline
      const operation = fetchRuntimeManifest(target, runtimeTag, 'test-token', testOptions(fetchImpl, { requestTimeoutMs: 1 }))
      const boundedOperation = Promise.race([
        operation,
        new Promise((_resolve, reject) => { deadline = setTimeout(() => reject(new Error('test body timeout')), 250) }),
      ])

      try {
        await expect(boundedOperation).rejects.toThrow(/已重试 3 次/)
      } finally {
        clearTimeout(deadline)
      }
      expect(bodyAttempts).toBe(4)
    },
  )
})

describe('currentPluginSha256', () => {
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

    expect(currentPluginSha256(repositoryRoot, runNpm)).toBe(
      createHash('sha256').update(tarballBytes).digest('hex'),
    )
    expect(calls.map((call) => call.args[1])).toEqual(['plugin:build', './packages/dsh-plugin-desktop'])
    expect(calls.every((call) => call.cwd === repositoryRoot)).toBe(true)
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
