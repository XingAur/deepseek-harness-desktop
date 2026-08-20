import { describe, expect, it } from 'vitest'
import { runtimeSourceConfig } from './write-runtime-source-config.mjs'

describe('runtime source config', () => {
  it('requires an immutable runtime manifest endpoint for release setup builds', () => {
    const config = runtimeSourceConfig({
      tag: 'runtime-v1.8.2',
      repository: 'anywhere-labs/deepseek-harness-desktop',
    })
    expect(config.endpoint).toBe(
      'https://github.com/anywhere-labs/deepseek-harness-desktop/releases/download/runtime-v1.8.2/runtime-windows-x86_64.json',
    )
    expect(config.allowedHosts).toEqual([
      'github.com',
      'objects.githubusercontent.com',
      'release-assets.githubusercontent.com',
    ])
  })

  it.each(['latest', 'main', 'https://example.com/runtime-v1.0.0', 'runtime-v1'])('rejects mutable or invalid tag %s', (tag) => {
    expect(() => runtimeSourceConfig({ tag, repository: 'owner/repo' })).toThrow(/固定/)
  })
})
