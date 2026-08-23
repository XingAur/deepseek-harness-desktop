import { describe, expect, it } from 'vitest'
import { productionUpdaterEndpoint, updaterConfig } from './write-updater-config.mjs'

describe('application updater release config', () => {
  it('writes Windows signed-updater settings and bundles the Runtime', () => {
    const config = updaterConfig({
      platform: 'windows-x86_64',
      publicKey: 'PUBLIC-KEY',
      endpoint: productionUpdaterEndpoint,
    })
    expect(config.bundle).toMatchObject({
      createUpdaterArtifacts: true,
      resources: { '../runtime/': 'runtime/' },
    })
    expect(config.plugins?.updater.pubkey).toBe('PUBLIC-KEY')
    expect(config.plugins?.updater.endpoints).toEqual([productionUpdaterEndpoint])
    expect(config.plugins?.updater.windows.installMode).toBe('passive')
  })

  it('writes an unsigned macOS bundle config without updater credentials', () => {
    expect(updaterConfig({ platform: 'darwin-aarch64' })).toEqual({
      bundle: {
        createUpdaterArtifacts: false,
        resources: { '../runtime/': 'runtime/' },
      },
    })
  })

  it('rejects missing Windows keys, alternate endpoints, and unsupported platforms', () => {
    expect(() => updaterConfig({ platform: 'windows-x86_64' })).toThrow(/public key/i)
    expect(() => updaterConfig({
      platform: 'windows-x86_64', publicKey: 'PUBLIC-KEY', endpoint: 'https://example.com/latest.json',
    })).toThrow(/固定/)
    expect(() => updaterConfig({ platform: 'linux-x86_64' as 'darwin-aarch64' })).toThrow(/platform/)
  })
})
