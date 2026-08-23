import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { inspectAssembledRuntimeCapabilities } from './runtime-build-capabilities.mjs'

const dshVersion = '0.1.1-rc.2'
const desktopPluginVersion = '0.3.2'

function writePackage(appDirectory: string, name: string, manifest: Record<string, unknown>) {
  const directory = join(appDirectory, 'node_modules', ...name.split('/'))
  mkdirSync(directory, { recursive: true })
  writeFileSync(join(directory, 'package.json'), `${JSON.stringify(manifest)}\n`)
  for (const value of Object.values(manifest.exports ?? {})) {
    for (const entrypoint of typeof value === 'string' ? [value] : Object.values(value as Record<string, unknown>)) writeEntrypoint(directory, entrypoint)
  }
  for (const entrypoint of Object.values(manifest.bin ?? {})) writeEntrypoint(directory, entrypoint)
  writeEntrypoint(directory, (manifest.dsh as { bundle?: { patch?: unknown } } | undefined)?.bundle?.patch)
}

function writeEntrypoint(directory: string, entrypoint: unknown) {
  if (typeof entrypoint !== 'string' || entrypoint === './package.json') return
  const file = join(directory, entrypoint)
  mkdirSync(join(file, '..'), { recursive: true })
  writeFileSync(file, '')
}

function bundleManifest(extraExports: Record<string, unknown> = {}) {
  return {
    version: dshVersion,
    type: 'module',
    license: 'MIT',
    exports: {
      '.': { default: './lib/index.js', types: './lib/types/index.d.ts' },
      './invariant': { default: './lib/invariant.js', types: './lib/types/invariant.d.ts' },
      './cordis.patch.yml': './cordis.patch.yml',
      './package.json': './package.json',
      ...extraExports,
    },
    dsh: { bundle: { patch: './cordis.patch.yml' } },
  }
}

function writeCompatibleAssembledApp(appDirectory: string) {
  writePackage(appDirectory, '@deepseek-ai/dsh', { version: dshVersion, type: 'module', license: 'MIT', bin: { dsh: 'lib/bin.js' } })
  writePackage(appDirectory, '@deepseek-ai/dsh-base', bundleManifest())
  writePackage(appDirectory, '@deepseek-ai/dsh-web-app', bundleManifest({ './startup': { default: './lib/startup.js', types: './lib/types/startup.d.ts' } }))
  writePackage(appDirectory, '@dsh/desktop-plugin', { version: desktopPluginVersion, type: 'module', license: 'MIT', exports: { '.': './lib/index.js', './client': './lib/client.js', './package.json': './package.json' }, dsh: { bundle: { patch: './cordis.patch.yml' } } })
  for (const name of ['@deepseek-ai/dsh-llm-pi-ai', '@deepseek-ai/dsh-skill', '@deepseek-ai/dsh-mcp-client']) {
    const manifest = bundleManifest()
    const { './cordis.patch.yml': _patch, ...exports } = manifest.exports
    const { dsh: _dsh, ...withoutDsh } = manifest
    writePackage(appDirectory, name, { ...withoutDsh, exports })
  }
}

describe('inspectAssembledRuntimeCapabilities', () => {
  it('probes an assembled app root without appending node_modules twice', () => {
    const appDirectory = mkdtempSync(join(tmpdir(), 'dsh-assembled-runtime-'))
    writeCompatibleAssembledApp(appDirectory)

    const report = inspectAssembledRuntimeCapabilities(appDirectory, { dshVersion, desktopPluginVersion })

    expect(report.packages).toEqual(expect.arrayContaining([
      expect.objectContaining({ name: '@deepseek-ai/dsh', status: 'compatible' }),
      expect.objectContaining({ name: '@dsh/desktop-plugin', status: 'compatible' }),
    ]))
  })
})
