import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import {
  REQUIRED_DSH_PEER_PACKAGES,
  assertRuntimePeerDependencies,
  findMissingRuntimePeers,
  runtimePeerDependencies,
} from './runtime-peer-dependencies.mjs'

const temporaryDirectories: string[] = []

afterEach(() => {
  for (const directory of temporaryDirectories.splice(0)) {
    rmSync(directory, { recursive: true, force: true })
  }
})

describe('Runtime peer dependencies', () => {
  it('pins the complete Runtime peer closure', () => {
    const dependencies = runtimePeerDependencies('0.1.1-rc.2')

    expect(REQUIRED_DSH_PEER_PACKAGES).toHaveLength(17)
    expect(dependencies).toMatchObject({
      '@deepseek-ai/cordis-plugin-group': '1.0.1',
      '@deepseek-ai/dsh-authorization': '0.1.1-rc.2',
      '@deepseek-ai/dsh-invariants': '0.1.1-rc.2',
      '@deepseek-ai/dsh-scope': '0.1.1-rc.2',
      '@deepseek-ai/dsh-workflow': '0.1.1-rc.2',
      react: '18.3.1',
      'react-dom': '18.3.1',
    })
    expect(Object.keys(dependencies)).toHaveLength(21)
  })

  it('rejects versions outside the reviewed 0.1 release-candidate line', () => {
    expect(() => runtimePeerDependencies('0.1.1')).toThrow('Unsupported DeepSeek Harness Runtime version')
    expect(() => runtimePeerDependencies('0.2.0-rc.1')).toThrow('Unsupported DeepSeek Harness Runtime version')
  })

  it('reports required missing peers and ignores optional peers', () => {
    const appDir = temporaryApp()
    writePackage(appDir, 'consumer', {
      name: 'consumer',
      version: '1.0.0',
      peerDependencies: { missing: '^1.0.0', optional: '^1.0.0' },
      peerDependenciesMeta: { optional: { optional: true } },
    })

    expect(findMissingRuntimePeers(appDir)).toEqual([
      { name: 'missing', requiredBy: ['consumer@1.0.0'] },
    ])
    expect(() => assertRuntimePeerDependencies(appDir)).toThrow('missing required by consumer@1.0.0')

    writePackage(appDir, 'missing', { name: 'missing', version: '1.2.0' })
    expect(findMissingRuntimePeers(appDir)).toEqual([])
  })
})

function temporaryApp() {
  const directory = mkdtempSync(join(tmpdir(), 'dsh-runtime-peers-'))
  temporaryDirectories.push(directory)
  mkdirSync(join(directory, 'node_modules'), { recursive: true })
  return directory
}

function writePackage(appDir: string, name: string, manifest: Record<string, unknown>) {
  const directory = join(appDir, 'node_modules', ...name.split('/'))
  mkdirSync(directory, { recursive: true })
  writeFileSync(join(directory, 'package.json'), `${JSON.stringify(manifest, null, 2)}\n`)
}
