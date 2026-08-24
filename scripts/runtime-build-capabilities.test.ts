import { execFileSync } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { inspectAssembledRuntimeCapabilities } from './runtime-build-capabilities.mjs'

const dshVersion = '0.1.1-rc.2'
const desktopPluginVersion = '0.3.2'
const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const npmCliPath = process.platform === 'win32' && process.env.npm_execpath?.toLowerCase().endsWith('.js')
  ? process.env.npm_execpath
  : undefined
const npmExecutable = npmCliPath ? process.execPath : process.platform === 'win32' ? 'npm.cmd' : 'npm'
const npmPrefix = npmCliPath ? [npmCliPath] : []
const npmShell = process.platform === 'win32' && !npmCliPath

function writePackage(appDirectory: string, name: string, manifest: Record<string, unknown>) {
  const directory = join(appDirectory, 'node_modules', ...name.split('/'))
  mkdirSync(directory, { recursive: true })
  const completeManifest: Record<string, unknown> = { name, ...manifest }
  writeFileSync(join(directory, 'package.json'), `${JSON.stringify(completeManifest)}\n`)
  for (const value of Object.values(completeManifest.exports ?? {})) {
    for (const entrypoint of typeof value === 'string' ? [value] : Object.values(value as Record<string, unknown>)) writeEntrypoint(directory, entrypoint)
  }
  for (const entrypoint of Object.values(completeManifest.bin ?? {})) writeEntrypoint(directory, entrypoint)
  writeEntrypoint(directory, (completeManifest.dsh as { bundle?: { patch?: unknown } } | undefined)?.bundle?.patch)
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
  writeUpstreamPackages(appDirectory)
  writePackage(appDirectory, '@dsh/desktop-plugin', { version: desktopPluginVersion, type: 'module', license: 'UNLICENSED', exports: { '.': './lib/index.js', './client': './lib/client.js', './package.json': './package.json' }, dsh: { bundle: { patch: './cordis.patch.yml' } } })
}

function writeUpstreamPackages(appDirectory: string) {
  writePackage(appDirectory, '@deepseek-ai/dsh', { version: dshVersion, type: 'module', license: 'MIT', bin: { dsh: 'lib/bin.js' } })
  writePackage(appDirectory, '@deepseek-ai/dsh-base', bundleManifest())
  writePackage(appDirectory, '@deepseek-ai/dsh-web-app', bundleManifest({ './startup': { default: './lib/startup.js', types: './lib/types/startup.d.ts' } }))
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
    try {
      writeCompatibleAssembledApp(appDirectory)
      const report = inspectAssembledRuntimeCapabilities(appDirectory, { dshVersion, desktopPluginVersion })
      expect(report.packages).toEqual(expect.arrayContaining([
        expect.objectContaining({ name: '@deepseek-ai/dsh', status: 'compatible' }),
        expect.objectContaining({ name: '@dsh/desktop-plugin', status: 'compatible' }),
      ]))
    } finally {
      rmSync(appDirectory, { recursive: true, force: true })
    }
  })

  it('cleans an assembled fixture when capability inspection rejects', () => {
    const appDirectory = mkdtempSync(join(tmpdir(), 'dsh-assembled-runtime-failure-'))
    try {
      expect(() => inspectAssembledRuntimeCapabilities(appDirectory, { dshVersion, desktopPluginVersion })).toThrow(/capability report/i)
    } finally {
      rmSync(appDirectory, { recursive: true, force: true })
    }
    expect(existsSync(appDirectory)).toBe(false)
  })

  it('accepts the actual packed private desktop plugin installed into an assembled app without network access', () => {
    const appDirectory = mkdtempSync(join(tmpdir(), 'dsh-packed-runtime-app-'))
    const packDirectory = mkdtempSync(join(tmpdir(), 'dsh-packed-plugin-'))
    try {
      writeCompatibleAssembledApp(appDirectory)
      rmSync(join(appDirectory, 'node_modules', '@dsh'), { recursive: true, force: true })
      writeFileSync(join(appDirectory, 'package.json'), JSON.stringify({ name: 'assembled-runtime-fixture', private: true }))
      const npmEnvironment = { ...process.env, npm_config_cache: join(packDirectory, 'npm-cache') }
      execFileSync(npmExecutable, [...npmPrefix, 'run', 'plugin:build'], { cwd: repositoryRoot, env: npmEnvironment, shell: npmShell, stdio: 'pipe' })
      execFileSync(npmExecutable, [...npmPrefix, 'pack', './packages/dsh-plugin-desktop', '--pack-destination', packDirectory], { cwd: repositoryRoot, env: npmEnvironment, shell: npmShell, stdio: 'pipe' })
      const tarball = join(packDirectory, 'dsh-desktop-plugin-0.3.2.tgz')
      execFileSync(npmExecutable, [...npmPrefix, 'install', '--offline', '--ignore-scripts', '--no-audit', '--no-fund', '--legacy-peer-deps', tarball], { cwd: appDirectory, env: npmEnvironment, shell: npmShell, stdio: 'pipe' })
      writeUpstreamPackages(appDirectory)

      const report = inspectAssembledRuntimeCapabilities(appDirectory, { dshVersion, desktopPluginVersion })

      expect(report.packages).toEqual(expect.arrayContaining([
        expect.objectContaining({ name: '@dsh/desktop-plugin', observedVersion: desktopPluginVersion, status: 'compatible' }),
      ]))
    } finally {
      rmSync(appDirectory, { recursive: true, force: true })
      rmSync(packDirectory, { recursive: true, force: true })
    }
  }, 20_000)
})
