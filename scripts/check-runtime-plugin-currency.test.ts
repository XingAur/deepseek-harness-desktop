import { createHash } from 'node:crypto'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { compareManifest, currentPluginSha256, sha256File } from './check-runtime-plugin-currency.mjs'

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
