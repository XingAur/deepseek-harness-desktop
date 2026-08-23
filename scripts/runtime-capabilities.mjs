import { existsSync, readFileSync, statSync } from 'node:fs'
import { isAbsolute, relative, resolve, sep } from 'node:path'
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

const cliEntrypoints = Object.freeze({ bin: 'lib/bin.js' })
const baseBundleEntrypoints = Object.freeze({
  '.': { default: './lib/index.js', types: './lib/types/index.d.ts' },
  './invariant': { default: './lib/invariant.js', types: './lib/types/invariant.d.ts' },
  './cordis.patch.yml': './cordis.patch.yml',
  './package.json': './package.json',
})
const optionalBundleEntrypoints = Object.freeze({
  '.': { default: './lib/index.js', types: './lib/types/index.d.ts' },
  './invariant': { default: './lib/invariant.js', types: './lib/types/invariant.d.ts' },
  './package.json': './package.json',
})
const packageContracts = Object.freeze({
  '@deepseek-ai/dsh': { entrypoints: cliEntrypoints, noExports: true, required: true, dshVersion: true },
  '@deepseek-ai/dsh-base': { entrypoints: baseBundleEntrypoints, bundle: true, required: true, dshVersion: true },
  '@deepseek-ai/dsh-web-app': { entrypoints: { ...baseBundleEntrypoints, './startup': { default: './lib/startup.js', types: './lib/types/startup.d.ts' } }, bundle: true, required: true, dshVersion: true },
  '@dsh/desktop-plugin': { entrypoints: { '.': './lib/index.js', './client': './lib/client.js', './package.json': './package.json' }, bundle: true, required: true },
  '@deepseek-ai/dsh-llm-pi-ai': { entrypoints: optionalBundleEntrypoints, dshVersion: true },
  '@deepseek-ai/dsh-skill': { entrypoints: optionalBundleEntrypoints, dshVersion: true },
  '@deepseek-ai/dsh-mcp-client': { entrypoints: optionalBundleEntrypoints, dshVersion: true },
})

export function inspectRuntimeCapabilities(runtimeRoot, { dshVersion, desktopPluginVersion }) {
  const packages = Object.entries(packageContracts).map(([name, contract]) => inspectPackage(
    runtimeRoot,
    name,
    contract.dshVersion ? dshVersion : desktopPluginVersion,
    contract.entrypoints,
    { noExports: contract.noExports, bundle: contract.bundle },
  ))
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
  if (report?.schemaVersion !== CAPABILITY_REPORT_SCHEMA_VERSION) throw new Error('Runtime capability compatibility failed: capability report schema version is invalid')
  const packages = Array.isArray(report.packages) ? report.packages : []
  const records = new Map()
  if (packages.length !== Object.keys(packageContracts).length) throw new Error('Runtime capability compatibility failed: capability package records are incomplete')
  for (const record of packages) {
    if (!isRecord(record) || typeof record.name !== 'string' || !packageContracts[record.name] || records.has(record.name)) {
      throw new Error('Runtime capability compatibility failed: capability package records are malformed or duplicated')
    }
    records.set(record.name, record)
  }
  const dshVersion = records.get('@deepseek-ai/dsh')?.observedVersion
  if (!isVersion(dshVersion)) throw new Error('Runtime capability compatibility failed: DSH observed version is invalid')
  for (const [name, contract] of Object.entries(packageContracts)) {
    const record = records.get(name)
    if (!record || !['compatible', 'missing', 'incompatible'].includes(record.status)) throw new Error(`Runtime capability compatibility failed: ${name}; capability record is invalid`)
    if (contract.required && record.status !== 'compatible') throw new Error(`Runtime capability compatibility failed: ${name}; ${record.reason ?? 'missing compatible capability record'}`)
    if (record.status === 'compatible') {
      if (!isVersion(record.observedVersion)) throw new Error(`Runtime capability compatibility failed: ${name}; observed version is invalid`)
      if (contract.dshVersion && record.observedVersion !== dshVersion) throw new Error(`Runtime capability compatibility failed: ${name}; observed version does not match DSH`)
      if (!sameValue(record.entrypoints, contract.entrypoints)) throw new Error(`Runtime capability compatibility failed: ${name}; validated entrypoints are invalid`)
      if (contract.bundle && record.bundlePatch !== './cordis.patch.yml') throw new Error(`Runtime capability compatibility failed: ${name}; validated bundle patch is invalid`)
    } else if (!sameValue(record.entrypoints, {}) || typeof record.reason !== 'string' || !record.reason) {
      throw new Error(`Runtime capability compatibility failed: ${name}; incompatible capability record is malformed`)
    }
  }
  assertCapabilityRecords(report.capabilities, records)
  if (!sameBundles(report.profileBundles, PROFILE_BUNDLES)) throw new Error('Runtime capability compatibility failed: exact desktop profile bundles are unavailable')
  return report
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

function entrypointPaths(value) {
  return typeof value === 'string' ? [value] : Object.values(value)
}

function assertCapabilityRecords(capabilities, records) {
  if (!isRecord(capabilities) || Object.keys(capabilities).length !== optionalCapabilities.length) throw new Error('Runtime capability compatibility failed: capability records are invalid')
  for (const [capability, name] of optionalCapabilities) {
    const value = capabilities[capability]
    if (!isRecord(value) || value.package !== name || value.available !== (records.get(name).status === 'compatible')) {
      throw new Error(`Runtime capability compatibility failed: ${capability} capability record is invalid`)
    }
  }
}

function isVersion(value) {
  return typeof value === 'string' && value.length > 0
}

function isRecord(value) {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isFileWithin(directory, relativePath, { pathImplementation = { resolve, relative, isAbsolute, sep }, statSync: inspectStatSync = statSync } = {}) {
  if (typeof relativePath !== 'string') return false
  const target = pathImplementation.resolve(directory, relativePath)
  const targetRelative = pathImplementation.relative(directory, target)
  if (targetRelative === '..' || targetRelative.startsWith(`..${pathImplementation.sep}`) || pathImplementation.isAbsolute(targetRelative)) return false
  try {
    return inspectStatSync(target).isFile()
  } catch {
    return false
  }
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
