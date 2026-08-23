import { describe, expect, it } from 'vitest'
import { productionUpdaterEndpoint, updaterConfig } from './write-updater-config.mjs'

describe('application updater release config', () => {
  it('writes the production public key and GitHub latest endpoint', () => {
    const config = updaterConfig('PUBLIC-KEY', productionUpdaterEndpoint)
    expect(config.bundle).toMatchObject({
      createUpdaterArtifacts: true,
      resources: ['../runtime'],
    })
    expect(config.plugins.updater.pubkey).toBe('PUBLIC-KEY')
    expect(config.plugins.updater.endpoints).toEqual([productionUpdaterEndpoint])
    expect(config.plugins.updater.windows.installMode).toBe('passive')
  })

  it('rejects missing keys and alternate endpoints', () => {
    expect(() => updaterConfig('')).toThrow(/不能为空/)
    expect(() => updaterConfig('PUBLIC-KEY', 'https://example.com/latest.json')).toThrow(/固定/)
  })
})
