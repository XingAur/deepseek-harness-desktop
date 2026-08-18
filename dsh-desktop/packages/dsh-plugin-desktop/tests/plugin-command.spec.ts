import { describe, expect, it } from 'vitest'
import { pluginArguments } from '../src/plugin-command'
import type { CatalogPlugin } from '../src/catalog'

const plugin: CatalogPlugin = {
  id: 'test/plugin', packageName: '@test/plugin', name: 'Test', description: 'Test plugin', publisher: 'Test',
  repository: 'https://github.com/test/plugin', installSpec: '@test/plugin@1.2.3', version: '1.2.3', dshRange: '>=0.1.0-rc.7',
  platforms: ['windows-x86_64', 'darwin-aarch64'], verified: true,
}

describe('pluginArguments', () => {
  it('uses official dsh plugin/pnpm forwarding arguments', () => {
    expect(pluginArguments('install', plugin)).toEqual(['plugin', '--profile', 'desktop', 'add', '@test/plugin@1.2.3'])
    expect(pluginArguments('update', plugin)).toEqual(['plugin', '--profile', 'desktop', 'update', '@test/plugin'])
    expect(pluginArguments('remove', plugin)).toEqual(['plugin', '--profile', 'desktop', 'remove', '@test/plugin'])
  })
})
