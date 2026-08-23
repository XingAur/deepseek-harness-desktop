import { existsSync, lstatSync, readFileSync, realpathSync, statSync } from 'node:fs'
import { isAbsolute, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

export const CAPABILITY_REPORT_SCHEMA_VERSION = 1
export const PROFILE_BUNDLES = Object.freeze([
  '@deepseek-ai/dsh-base',
  '@deepseek-ai/dsh-web-app',
  '@dsh/desktop-plugin',
])
export const CAPABILITY_REASON_CODES = Object.freeze([
  'MISSING_PACKAGE_JSON',
  'PACKAGE_PATH_INVALID',
  'MANIFEST_INVALID',
  'MANIFEST_NAME_INVALID',
  'VERSION_MISMATCH',
  'TYPE_INVALID',
  'LICENSE_INVALID',
  'EXPORTS_INVALID',
  'ENTRYPOINT_INVALID',
  'BUNDLE_PATCH_INVALID',
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
  '@deepseek-ai/dsh': { entrypoints: cliEntrypoints, license: 'MIT', noExports: true, required: true, dshVersion: true },
  '@deepseek-ai/dsh-base': { entrypoints: baseBundleEntrypoints, bundle: true, license: 'MIT', required: true, dshVersion: true },
  '@deepseek-ai/dsh-web-app': { entrypoints: { ...baseBundleEntrypoints, './startup': { default: './lib/startup.js', types: './lib/types/startup.d.ts' } }, bundle: true, license: 'MIT', required: true, dshVersion: true },
  '@dsh/desktop-plugin': { entrypoints: { '.': './lib/index.js', './client': './lib/client.js', './package.json': './package.json' }, bundle: true, license: 'UNLICENSED', required: true },
  '@deepseek-ai/dsh-llm-pi-ai': { entrypoints: optionalBundleEntrypoints, license: 'MIT', dshVersion: true },
  '@deepseek-ai/dsh-skill': { entrypoints: optionalBundleEntrypoints, license: 'MIT', dshVersion: true },
  '@deepseek-ai/dsh-mcp-client': { entrypoints: optionalBundleEntrypoints, license: 'MIT', dshVersion: true },
})
const defaultPath = { resolve, relative, isAbsolute, sep }
const defaultFs = { existsSync, lstatSync, readFileSync, realpathSync, statSync }

export function inspectRuntimeCapabilities(runtimeRoot, expectedVersions, adapters = {}) {
  assertExpectedVersions(expectedVersions)
  const fs = { ...defaultFs, ...adapters }
  const pathImplementation = adapters.pathImplementation ?? defaultPath
  const packages = Object.entries(packageContracts).map(([name, contract]) => inspectPackage(
    runtimeRoot, name, expectedVersion(contract, expectedVersions), contract.entrypoints,
    { noExports: contract.noExports, bundle: contract.bundle, license: contract.license }, fs, pathImplementation,
  ))
  const byName = new Map(packages.map((record) => [record.name, record]))
  const profileBundles = PROFILE_BUNDLES.every((name) => byName.get(name)?.status === 'compatible') ? [...PROFILE_BUNDLES] : undefined
  return { schemaVersion: CAPABILITY_REPORT_SCHEMA_VERSION, packages,
    capabilities: Object.fromEntries(optionalCapabilities.map(([capability, name]) => [capability, { package: name, available: byName.get(name)?.status === 'compatible' }])),
    ...(profileBundles ? { profileBundles } : {}), }
}

export function assertRuntimeCapabilities(report, expectedVersions) {
  assertExpectedVersions(expectedVersions)
  failIf(report?.schemaVersion !== CAPABILITY_REPORT_SCHEMA_VERSION || !hasExactKeys(report, ['capabilities', 'packages', 'profileBundles', 'schemaVersion']), 'capability report is invalid')
  failIf(!Array.isArray(report.packages) || report.packages.length !== Object.keys(packageContracts).length, 'capability package records are incomplete')
  const records = new Map()
  for (const record of report.packages) {
    failIf(!isRecord(record) || typeof record.name !== 'string' || !packageContracts[record.name] || records.has(record.name), 'capability package records are malformed or duplicated')
    records.set(record.name, record)
  }
  for (const [name, contract] of Object.entries(packageContracts)) validateRecord(records.get(name), contract, expectedVersion(contract, expectedVersions))
  assertCapabilityRecords(report.capabilities, records)
  failIf(!sameBundles(report.profileBundles, PROFILE_BUNDLES), 'exact desktop profile bundles are unavailable')
  return report
}

function validateRecord(record, contract, version) {
  failIf(!record || !['compatible', 'missing', 'incompatible'].includes(record.status), 'capability record is invalid')
  if (record.status === 'compatible') {
    failIf(!hasExactKeys(record, ['entrypoints', 'name', 'observedVersion', 'status', ...(contract.bundle ? ['bundlePatch'] : [])]), 'compatible capability record is malformed')
    failIf(record.observedVersion !== version || !sameValue(record.entrypoints, contract.entrypoints) || (contract.bundle && record.bundlePatch !== './cordis.patch.yml'), 'compatible capability record is incompatible')
    return
  }
  failIf(!hasExactKeys(record, ['entrypoints', 'name', 'observedVersion', 'reasonCode', 'status']) || !sameValue(record.entrypoints, {}) || !CAPABILITY_REASON_CODES.includes(record.reasonCode), 'incompatible capability record is malformed')
  failIf(record.status === 'missing' && record.observedVersion !== null, 'missing capability record is malformed')
  failIf(record.status === 'incompatible' && record.observedVersion !== null && !isVersion(record.observedVersion), 'incompatible capability record is malformed')
  failIf(contract.required, 'required capability record is not compatible')
}

function inspectPackage(runtimeRoot, name, expectedVersion, entrypoints, options, fs, pathImplementation) {
  const location = locatePackage(runtimeRoot, name, fs, pathImplementation)
  if (location.kind === 'missing') return failure(name, 'missing', null, 'MISSING_PACKAGE_JSON')
  if (location.kind === 'invalid') return failure(name, 'incompatible', null, 'PACKAGE_PATH_INVALID')
  let manifest
  try { manifest = JSON.parse(fs.readFileSync(location.packagePath, 'utf8')) } catch { return failure(name, 'incompatible', null, 'MANIFEST_INVALID') }
  const observedVersion = isVersion(manifest?.version) ? manifest.version : null
  if (manifest.name !== name) return failure(name, 'incompatible', observedVersion, 'MANIFEST_NAME_INVALID')
  if (observedVersion !== expectedVersion) return failure(name, 'incompatible', observedVersion, 'VERSION_MISMATCH')
  if (manifest.type !== 'module') return failure(name, 'incompatible', observedVersion, 'TYPE_INVALID')
  if (manifest.license !== options.license) return failure(name, 'incompatible', observedVersion, 'LICENSE_INVALID')
  if (options.noExports && manifest.exports !== undefined) return failure(name, 'incompatible', observedVersion, 'EXPORTS_INVALID')
  const actualEntrypoints = options.noExports ? { bin: manifest.bin?.dsh } : manifest.exports
  for (const [key, expected] of Object.entries(entrypoints)) {
    if (!sameValue(actualEntrypoints?.[key], expected) || entrypointPaths(expected).some((entrypoint) => !isFileWithin(location.directory, entrypoint, { pathImplementation, ...fs }))) return failure(name, 'incompatible', observedVersion, 'ENTRYPOINT_INVALID')
  }
  const bundlePatch = options.bundle ? manifest.dsh?.bundle?.patch : undefined
  if (options.bundle && (bundlePatch !== './cordis.patch.yml' || !isFileWithin(location.directory, bundlePatch, { pathImplementation, ...fs }))) return failure(name, 'incompatible', observedVersion, 'BUNDLE_PATCH_INVALID')
  return { name, observedVersion, status: 'compatible', entrypoints, ...(options.bundle ? { bundlePatch } : {}) }
}

function locatePackage(runtimeRoot, name, fs, pathImplementation) {
  const nodeModules = pathImplementation.resolve(runtimeRoot, 'node_modules')
  try {
    if (!fs.existsSync(nodeModules)) return { kind: 'missing' }
    if (fs.lstatSync(nodeModules).isSymbolicLink()) return { kind: 'invalid' }
    const canonicalNodeModules = fs.realpathSync(nodeModules)
    let lexical = nodeModules
    let canonical = canonicalNodeModules
    for (const component of name.split('/')) {
      lexical = pathImplementation.resolve(lexical, component)
      if (!isWithin(nodeModules, lexical, pathImplementation)) return { kind: 'invalid' }
      if (!fs.existsSync(lexical)) return { kind: 'missing' }
      if (fs.lstatSync(lexical).isSymbolicLink()) return { kind: 'invalid' }
      const actual = fs.realpathSync(lexical)
      const assigned = pathImplementation.resolve(canonical, component)
      if (actual !== assigned || !isWithin(canonicalNodeModules, actual, pathImplementation)) return { kind: 'invalid' }
      canonical = actual
    }
    const packagePath = pathImplementation.resolve(lexical, 'package.json')
    if (!fs.existsSync(packagePath)) return { kind: 'missing' }
    if (fs.lstatSync(packagePath).isSymbolicLink()) return { kind: 'invalid' }
    const canonicalPackagePath = fs.realpathSync(packagePath)
    if (canonicalPackagePath !== pathImplementation.resolve(canonical, 'package.json') || !isWithin(canonical, canonicalPackagePath, pathImplementation)) return { kind: 'invalid' }
    return { kind: 'found', directory: canonical, packagePath: canonicalPackagePath }
  } catch { return { kind: 'invalid' }
  }
}

function expectedVersion(contract, versions) { return contract.dshVersion ? versions.dshVersion : versions.desktopPluginVersion }
function assertExpectedVersions(value) { failIf(!hasExactKeys(value, ['desktopPluginVersion', 'dshVersion']) || !isVersion(value.dshVersion) || !isVersion(value.desktopPluginVersion), 'expected versions are invalid') }
function failure(name, status, observedVersion, reasonCode) { return { name, observedVersion, status, entrypoints: {}, reasonCode } }
function entrypointPaths(value) { return typeof value === 'string' ? [value] : Object.values(value) }
function assertCapabilityRecords(capabilities, records) {
  failIf(!hasExactKeys(capabilities, optionalCapabilities.map(([capability]) => capability)), 'capability records are invalid')
  for (const [capability, name] of optionalCapabilities) {
    const value = capabilities[capability]
    failIf(!hasExactKeys(value, ['available', 'package']) || value.package !== name || value.available !== (records.get(name).status === 'compatible'), 'capability record is invalid')
  }
}
function failIf(condition, detail) { if (condition) throw new Error(`Runtime capability compatibility failed: ${detail}`) }
function isVersion(value) { return typeof value === 'string' && value.length > 0 && value.length <= 128 }
function isRecord(value) { return typeof value === 'object' && value !== null && !Array.isArray(value) }
function hasExactKeys(value, expectedKeys) { if (!isRecord(value)) return false; const keys = Object.keys(value).sort(); const expected = [...expectedKeys].sort(); return keys.length === expected.length && keys.every((key, index) => key === expected[index]) }
function isWithin(directory, target, pathImplementation) { const candidate = pathImplementation.relative(directory, target); return candidate !== '..' && !candidate.startsWith(`..${pathImplementation.sep}`) && !pathImplementation.isAbsolute(candidate) }

export function isFileWithin(directory, relativePath, { pathImplementation = defaultPath, statSync: inspectStatSync = statSync, lstatSync: inspectLstatSync = lstatSync, realpathSync: inspectRealpathSync = realpathSync } = {}) {
  if (typeof relativePath !== 'string') return false
  const target = pathImplementation.resolve(directory, relativePath)
  if (!isWithin(directory, target, pathImplementation)) return false
  try { if (inspectLstatSync(target).isSymbolicLink()) return false; const canonicalDirectory = inspectRealpathSync(directory); const canonicalTarget = inspectRealpathSync(target); return isWithin(canonicalDirectory, canonicalTarget, pathImplementation) && inspectStatSync(canonicalTarget).isFile() } catch { return false }
}
function sameBundles(value, expected) { return Array.isArray(value) && value.length === expected.length && value.every((bundle, index) => bundle === expected[index]) }
function sameValue(actual, expected) { if (actual === expected) return true; if (!isRecord(actual) || !isRecord(expected)) return false; const actualKeys = Object.keys(actual).sort(); const expectedKeys = Object.keys(expected).sort(); return actualKeys.length === expectedKeys.length && actualKeys.every((key, index) => key === expectedKeys[index] && sameValue(actual[key], expected[key])) }

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : ''
if (invokedPath === resolve(fileURLToPath(import.meta.url))) {
  try { const root = process.argv.find((argument) => argument.startsWith('--runtime-root='))?.slice('--runtime-root='.length); if (!root) throw new Error('--runtime-root is required'); const { loadReleaseVersions } = await import('./release-versions.mjs'); const versions = loadReleaseVersions(); const desktopPluginVersion = JSON.parse(readFileSync(resolve('packages/dsh-plugin-desktop/package.json'), 'utf8')).version; process.stdout.write(`${JSON.stringify(inspectRuntimeCapabilities(resolve(root), { dshVersion: versions.dshVersion, desktopPluginVersion }), null, 2)}\n`) } catch (cause) { process.stderr.write(`${cause instanceof Error ? cause.message : String(cause)}\n`); process.exitCode = 1 }
}
