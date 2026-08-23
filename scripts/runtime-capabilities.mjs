import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const CAPABILITY_REPORT_SCHEMA_VERSION = 1
export const PROFILE_BUNDLES = Object.freeze([
  '@deepseek-ai/dsh-base',
  '@deepseek-ai/dsh-web-app',
  '@dsh/desktop-plugin',
])

const optionalCapabilities = Object.freeze([
  ['apiProvider', '@deepseek-ai/dsh-llm-pi-ai'],
  ['skill', '@deepseek-ai/dsh-skill'],
  ['mcp', '@deepseek-ai/dsh-mcp-client'],
])

export function inspectRuntimeCapabilities(runtimeRoot, { dshVersion, desktopPluginVersion }) {
  const packages = [
    inspectCli(runtimeRoot, dshVersion),
    inspectBundle(runtimeRoot, '@deepseek-ai/dsh-base', dshVersion, bundleEntrypoints()),
    inspectBundle(runtimeRoot, '@deepseek-ai/dsh-web-app', dshVersion, bundleEntrypoints({ './startup': { default: './lib/startup.js', types: './lib/types/startup.d.ts' } })),
    inspectBundle(runtimeRoot, '@dsh/desktop-plugin', desktopPluginVersion, { '.': './lib/index.js', './client': './lib/client.js', './package.json': './package.json' }),
    ...optionalCapabilities.map(([, name]) => inspectPackage(runtimeRoot, name, dshVersion, optionalEntrypoints())),
  ]
  const byName = new Map(packages.map((record) => [record.name, record]))
  const profileBundles = PROFILE_BUNDLES.every((name) => byName.get(name)?.status === 'compatible') ? [...PROFILE_BUNDLES] : undefined
  return {
    schemaVersion: CAPABILITY_REPORT_SCHEMA_VERSION,
    packages,
    capabilities: Object.fromEntries(optionalCapabilities.map(([capability, name]) => [capability, { package: name, available: byName.get(name)?.status === 'compatible' }])),
    ...(profileBundles ? { profileBundles } : {}),
  }
}

export function assertRuntimeCapabilities(report) {
  const required = ['@deepseek-ai/dsh', ...PROFILE_BUNDLES]
  const packages = Array.isArray(report?.packages) ? report.packages : []
  const incompatible = required.map((name) => ({ name, record: packages.find((record) => record.name === name) })).find(({ record }) => record?.status !== 'compatible')
  if (incompatible) throw new Error(`Runtime capability compatibility failed: ${incompatible.name}; ${incompatible.record?.reason ?? 'missing compatible capability record'}`)
  if (!sameBundles(report.profileBundles, PROFILE_BUNDLES)) throw new Error('Runtime capability compatibility failed: exact desktop profile bundles are unavailable')
  return report
}

function inspectCli(runtimeRoot, expectedVersion) {
  return inspectPackage(runtimeRoot, '@deepseek-ai/dsh', expectedVersion, { bin: 'lib/bin.js' }, { noExports: true })
}

function inspectBundle(runtimeRoot, name, expectedVersion, entrypoints) {
  return inspectPackage(runtimeRoot, name, expectedVersion, entrypoints, { bundle: true })
}

function inspectPackage(runtimeRoot, name, expectedVersion, entrypoints, options = {}) {
  const directory = resolve(runtimeRoot, 'node_modules', ...name.split('/'))
  const packagePath = resolve(directory, 'package.json')
  if (!existsSync(packagePath)) return record(name, undefined, 'missing', {}, undefined, 'package.json is missing')
  let manifest
  try {
    manifest = JSON.parse(readFileSync(packagePath, 'utf8'))
  } catch {
    return record(name, undefined, 'incompatible', {}, undefined, 'package.json is not valid JSON')
  }
  const observedVersion = typeof manifest.version === 'string' ? manifest.version : undefined
  if (observedVersion !== expectedVersion) return record(name, observedVersion, 'incompatible', {}, undefined, `version expected ${expectedVersion}; observed ${observedVersion ?? 'missing'}`)
  if (manifest.type !== 'module') return record(name, observedVersion, 'incompatible', {}, undefined, `type expected module; observed ${String(manifest.type)}`)
  if (manifest.license !== 'MIT') return record(name, observedVersion, 'incompatible', {}, undefined, `license expected MIT; observed ${String(manifest.license)}`)
  if (options.noExports && manifest.exports !== undefined) return record(name, observedVersion, 'incompatible', {}, undefined, 'exports expected absent; observed present')
  const actualEntrypoints = options.noExports ? { bin: manifest.bin?.dsh } : manifest.exports
  for (const [key, expected] of Object.entries(entrypoints)) {
    const observed = actualEntrypoints?.[key]
    if (!sameValue(observed, expected)) {
      return record(name, observedVersion, 'incompatible', {}, undefined, `export ${key} expected ${stableJson(expected)}; observed ${stableJson(observed)}`)
    }
    for (const path of entrypointPaths(expected)) {
      if (!isFileWithin(directory, path)) return record(name, observedVersion, 'incompatible', {}, undefined, `entrypoint ${key} expected file ${path}; observed missing`)
    }
  }
  let bundlePatch
  if (options.bundle) {
    bundlePatch = manifest.dsh?.bundle?.patch
    if (bundlePatch !== './cordis.patch.yml' || !isFileWithin(directory, bundlePatch)) {
      return record(name, observedVersion, 'incompatible', {}, undefined, `bundle patch expected ./cordis.patch.yml; observed ${JSON.stringify(bundlePatch)}`)
    }
  }
  return record(name, observedVersion, 'compatible', entrypoints, bundlePatch)
}

function bundleEntrypoints(extra = {}) {
  return {
    '.': { default: './lib/index.js', types: './lib/types/index.d.ts' },
    './invariant': { default: './lib/invariant.js', types: './lib/types/invariant.d.ts' },
    './cordis.patch.yml': './cordis.patch.yml',
    './package.json': './package.json',
    ...extra,
  }
}

function optionalEntrypoints() {
  const entries = bundleEntrypoints()
  delete entries['./cordis.patch.yml']
  return entries
}

function entrypointPaths(value) {
  return typeof value === 'string' ? [value] : Object.values(value)
}

function isFileWithin(directory, relativePath) {
  if (relativePath === './package.json') return existsSync(resolve(directory, 'package.json'))
  if (typeof relativePath !== 'string') return false
  const target = resolve(directory, relativePath)
  return target.startsWith(`${directory}/`) && existsSync(target)
}

function record(name, observedVersion, status, entrypoints, bundlePatch, reason) {
  return {
    name,
    observedVersion: observedVersion ?? null,
    status,
    entrypoints,
    ...(bundlePatch ? { bundlePatch } : {}),
    ...(reason ? { reason } : {}),
  }
}

function sameBundles(value, expected) {
  return Array.isArray(value) && value.length === expected.length && value.every((bundle, index) => bundle === expected[index])
}

function sameValue(actual, expected) {
  if (actual === expected) return true
  if (typeof actual !== 'object' || actual === null || typeof expected !== 'object' || expected === null) return false
  const actualKeys = Object.keys(actual).sort()
  const expectedKeys = Object.keys(expected).sort()
  return actualKeys.length === expectedKeys.length && actualKeys.every((key, index) => key === expectedKeys[index] && sameValue(actual[key], expected[key]))
}

function stableJson(value) {
  if (typeof value !== 'object' || value === null) return JSON.stringify(value)
  return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(',')}}`
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : ''
if (invokedPath === resolve(fileURLToPath(import.meta.url))) {
  try {
    const root = process.argv.find((argument) => argument.startsWith('--runtime-root='))?.slice('--runtime-root='.length)
    if (!root) throw new Error('--runtime-root is required')
    const { loadReleaseVersions } = await import('./release-versions.mjs')
    const versions = loadReleaseVersions()
    const desktopPluginVersion = JSON.parse(readFileSync(resolve('packages/dsh-plugin-desktop/package.json'), 'utf8')).version
    process.stdout.write(`${JSON.stringify(inspectRuntimeCapabilities(resolve(root), { dshVersion: versions.dshVersion, desktopPluginVersion }), null, 2)}\n`)
  } catch (cause) {
    process.stderr.write(`${cause instanceof Error ? cause.message : String(cause)}\n`)
    process.exitCode = 1
  }
}
