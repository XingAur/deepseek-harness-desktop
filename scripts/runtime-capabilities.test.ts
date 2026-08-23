import { mkdirSync, mkdtempSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, win32 } from 'node:path'
import { describe, expect, it } from 'vitest'
import { DESKTOP_BUNDLES } from './desktop-profile.mjs'
import { inspectRuntimeCapabilities, isFileWithin } from './runtime-capabilities.mjs'

const dshVersion = '0.1.1-rc.2'
const desktopPluginVersion = '0.3.2'

function runtimeFixture() {
  return mkdtempSync(join(tmpdir(), 'dsh-runtime-capabilities-'))
}

function writePackage(root: string, name: string, manifest: Record<string, unknown>) {
  const directory = join(root, 'node_modules', ...name.split('/'))
  mkdirSync(directory, { recursive: true })
  const completeManifest: Record<string, unknown> = { name, ...manifest }
  writeFileSync(join(directory, 'package.json'), `${JSON.stringify(completeManifest, null, 2)}\n`)
  for (const value of Object.values(completeManifest.exports ?? {})) {
    for (const path of typeof value === 'string' ? [value] : Object.values(value as Record<string, unknown>)) writeEntrypoint(directory, path)
  }
  for (const path of Object.values(completeManifest.bin ?? {})) writeEntrypoint(directory, path)
  const patch = (completeManifest.dsh as { bundle?: { patch?: unknown } } | undefined)?.bundle?.patch
  writeEntrypoint(directory, patch)
}

function writeEntrypoint(directory: string, path: unknown) {
  if (typeof path !== 'string' || path === './package.json') return
  const file = join(directory, path)
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

function optionalManifest() {
  const manifest = bundleManifest()
  const { './cordis.patch.yml': _patch, ...exports } = manifest.exports
  const { dsh: _dsh, ...withoutDsh } = manifest
  return { ...withoutDsh, exports }
}

function compatibleRuntime(root: string, missingOptionalPackage?: string) {
  writePackage(root, '@deepseek-ai/dsh', { version: dshVersion, type: 'module', license: 'MIT', bin: { dsh: 'lib/bin.js' } })
  writePackage(root, '@deepseek-ai/dsh-base', bundleManifest())
  writePackage(root, '@deepseek-ai/dsh-web-app', bundleManifest({ './startup': { default: './lib/startup.js', types: './lib/types/startup.d.ts' } }))
  writePackage(root, '@dsh/desktop-plugin', {
    version: desktopPluginVersion,
    type: 'module',
    license: 'UNLICENSED',
    exports: { '.': './lib/index.js', './client': './lib/client.js', './package.json': './package.json' },
    dsh: { bundle: { patch: './cordis.patch.yml' } },
  })
  for (const name of ['@deepseek-ai/dsh-llm-pi-ai', '@deepseek-ai/dsh-skill', '@deepseek-ai/dsh-mcp-client']) {
    if (name !== missingOptionalPackage) writePackage(root, name, optionalManifest())
  }
}

function inspect(root: string) {
  return inspectRuntimeCapabilities(root, { dshVersion, desktopPluginVersion })
}

describe('inspectRuntimeCapabilities', () => {
  it('reports the pinned upstream DSH public exports and deterministic profile bundles', () => {
    const root = runtimeFixture()
    compatibleRuntime(root)

    const report = inspect(root)

    expect(report).toMatchObject({ schemaVersion: 1, profileBundles: DESKTOP_BUNDLES })
    expect(report.packages).toEqual(expect.arrayContaining([
      expect.objectContaining({ name: '@deepseek-ai/dsh', observedVersion: dshVersion, status: 'compatible', entrypoints: { bin: 'lib/bin.js' } }),
      expect.objectContaining({ name: '@deepseek-ai/dsh-base', status: 'compatible', bundlePatch: './cordis.patch.yml', entrypoints: expect.objectContaining({ '.': expect.objectContaining({ default: './lib/index.js', types: './lib/types/index.d.ts' }), './invariant': expect.objectContaining({ default: './lib/invariant.js', types: './lib/types/invariant.d.ts' }) }) }),
      expect.objectContaining({ name: '@deepseek-ai/dsh-web-app', status: 'compatible', entrypoints: expect.objectContaining({ './startup': expect.objectContaining({ default: './lib/startup.js', types: './lib/types/startup.d.ts' }) }) }),
      expect.objectContaining({ name: '@dsh/desktop-plugin', observedVersion: desktopPluginVersion, status: 'compatible', entrypoints: { '.': './lib/index.js', './client': './lib/client.js', './package.json': './package.json' } }),
    ]))
    expect(report.capabilities).toEqual({
      apiProvider: { package: '@deepseek-ai/dsh-llm-pi-ai', available: true },
      skill: { package: '@deepseek-ai/dsh-skill', available: true },
      mcp: { package: '@deepseek-ai/dsh-mcp-client', available: true },
    })
  })

  it('accepts public export maps whose object keys use a different order', () => {
    const root = runtimeFixture()
    compatibleRuntime(root)
    const manifest = bundleManifest()
    manifest.exports['.'] = { types: './lib/types/index.d.ts', default: './lib/index.js' }
    writePackage(root, '@deepseek-ai/dsh-base', manifest)

    const report = inspect(root)

    expect(report.packages).toEqual(expect.arrayContaining([expect.objectContaining({ name: '@deepseek-ai/dsh-base', status: 'compatible' })]))
  })

  it.each([
    ['missing package', '@deepseek-ai/dsh-skill', 'missing', (root: string) => compatibleRuntime(root, '@deepseek-ai/dsh-skill')],
    ['renamed export', '@deepseek-ai/dsh-skill', 'incompatible', (root: string) => { compatibleRuntime(root); writePackage(root, '@deepseek-ai/dsh-skill', { ...optionalManifest(), exports: { '.': { default: './lib/index.js', types: './lib/types/index.d.ts' }, './renamed-invariant': { default: './lib/invariant.js', types: './lib/types/invariant.d.ts' }, './package.json': './package.json' } }) }],
    ['wrong major version', '@deepseek-ai/dsh-mcp-client', 'incompatible', (root: string) => { compatibleRuntime(root); writePackage(root, '@deepseek-ai/dsh-mcp-client', { ...optionalManifest(), version: '1.0.0' }) }],
    ['malformed package metadata', '@deepseek-ai/dsh-llm-pi-ai', 'incompatible', (root: string) => { compatibleRuntime(root); writeFileSync(join(root, 'node_modules', '@deepseek-ai', 'dsh-llm-pi-ai', 'package.json'), '{not-json') }],
  ])('marks %s deterministically without mounting it', (_label, name, expectedStatus, setup) => {
    const root = runtimeFixture()
    setup(root)
    const report = inspect(root)
    expect(report.packages).toEqual(expect.arrayContaining([expect.objectContaining({ name, status: expectedStatus, reason: expect.any(String) })]))
    expect(report.profileBundles).toEqual(DESKTOP_BUNDLES)
  })

  it.each([
    ['required package', '@deepseek-ai/dsh-base'],
    ['optional package', '@deepseek-ai/dsh-skill'],
  ])('rejects a spoofed manifest name for a %s', (_label, name) => {
    const root = runtimeFixture()
    compatibleRuntime(root)
    const manifest = name === '@deepseek-ai/dsh-base' ? bundleManifest() : optionalManifest()
    writePackage(root, name, { ...manifest, name: '@spoofed/package' })

    const report = inspect(root)

    expect(report.packages).toEqual(expect.arrayContaining([
      expect.objectContaining({ name, status: 'incompatible', reason: expect.stringMatching(/name expected/i) }),
    ]))
  })

  it('never mounts an optional package solely because its directory exists', () => {
    const root = runtimeFixture()
    compatibleRuntime(root)

    const report = inspect(root)

    expect(report.profileBundles).toEqual(DESKTOP_BUNDLES)
    expect(report.profileBundles).not.toContain('@deepseek-ai/dsh-skill')
  })

  it('uses platform-safe relative containment and requires a regular file for entrypoints', () => {
    const directory = 'C:\\runtime\\app\\node_modules\\@deepseek-ai\\dsh-base'
    const files = new Set(['C:\\runtime\\app\\node_modules\\@deepseek-ai\\dsh-base\\lib\\index.js'])
    const stat = (path: string) => ({ isFile: () => files.has(path) })

    const adapter = {
      pathImplementation: win32,
      statSync: stat,
      lstatSync: () => ({ isSymbolicLink: () => false }),
      realpathSync: (path: string) => path,
    }

    expect(isFileWithin(directory, './lib/index.js', adapter)).toBe(true)
    expect(isFileWithin(directory, '..\\outside.js', adapter)).toBe(false)
    expect(isFileWithin(directory, './lib', adapter)).toBe(false)
  })

  it('rejects an intermediate POSIX symlink that escapes the package root', () => {
    const root = runtimeFixture()
    const outside = mkdtempSync(join(tmpdir(), 'dsh-runtime-outside-'))
    writeFileSync(join(outside, 'secret.js'), '')
    symlinkSync(outside, join(root, 'lib'))

    expect(isFileWithin(root, './lib/secret.js')).toBe(false)
  })

  it('rejects a final POSIX symlink even when it points inside the package root', () => {
    const root = runtimeFixture()
    mkdirSync(join(root, 'lib'), { recursive: true })
    writeFileSync(join(root, 'lib', 'target.js'), '')
    symlinkSync(join(root, 'lib', 'target.js'), join(root, 'lib', 'index.js'))

    expect(isFileWithin(root, './lib/index.js')).toBe(false)
  })

  it('keeps canonical containment testable with Windows path semantics', () => {
    const directory = 'C:\\runtime\\app\\node_modules\\@deepseek-ai\\dsh-base'

    expect(isFileWithin(directory, './lib/index.js', {
      pathImplementation: win32,
      statSync: () => ({ isFile: () => true }),
      lstatSync: () => ({ isSymbolicLink: () => false }),
      realpathSync: (path: string) => path === directory ? directory : 'D:\\escaped\\index.js',
    })).toBe(false)
  })
})
