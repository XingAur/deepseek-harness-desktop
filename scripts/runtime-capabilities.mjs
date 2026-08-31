import { createHash } from 'node:crypto'
import { existsSync, lstatSync, readFileSync, realpathSync, statSync } from 'node:fs'
import { isAbsolute, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

export const CAPABILITY_REPORT_SCHEMA_VERSION = 2
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
  'DEPENDENCY_RANGE_INVALID',
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
const featureGroupMatchers = Object.freeze({
  modelProvider: (name) => /(?:dsh-llm|provider)/.test(name),
  sessionTrajectory: (name) => /(?:dsh-session|trajectory)/.test(name),
  planGoal: (name) => /(?:dsh-plan-mode|dsh-goal)/.test(name),
  jobsScheduling: (name) => /(?:dsh-jobs|dsh-schedule|tool-jobs)/.test(name),
  skill: (name) => /dsh-skill/.test(name),
  mcp: (name) => /dsh-mcp-client/.test(name),
  subagent: (name) => /subagent/.test(name),
  workflow: (name) => /workflow/.test(name),
  approvalQuestions: (name) => /(?:user-approval|ask-user)/.test(name),
  filesystemShell: (name) => /(?:tool-fs|fs-local|terminal|tool-bash|pwsh)/.test(name),
  webTools: (name) => /tool-web/.test(name),
  hooksWebhooks: (name) => /(?:hooks-|webhook)/.test(name),
  sessionsSettings: (name) => /(?:dsh-session|dsh-settings)/.test(name),
  officialWebUi: (name) => name === '@deepseek-ai/dsh-web-app',
})
const packageNamePattern = /^(?:@[a-z0-9][a-z0-9._-]*\/)?[a-z0-9][a-z0-9._-]*$/
const exactSemVer = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/
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
  const officialClosure = inspectOfficialClosure(runtimeRoot, expectedVersions, fs, pathImplementation)
  const profileBundles = PROFILE_BUNDLES.every((name) => byName.get(name)?.status === 'compatible') ? [...PROFILE_BUNDLES] : undefined
  return { schemaVersion: CAPABILITY_REPORT_SCHEMA_VERSION, packages,
    capabilities: Object.fromEntries(optionalCapabilities.map(([capability, name]) => [capability, { package: name, available: byName.get(name)?.status === 'compatible' }])),
    officialClosure,
    featureGroups: buildFeatureGroups(featureRecords(officialClosure.packages, packages)),
    ...(profileBundles ? { profileBundles } : {}), }
}

export function assertRuntimeCapabilities(report, expectedVersions) {
  assertExpectedVersions(expectedVersions)
  failIf(report?.schemaVersion !== CAPABILITY_REPORT_SCHEMA_VERSION || !hasExactKeys(report, ['capabilities', 'featureGroups', 'officialClosure', 'packages', 'profileBundles', 'schemaVersion']), 'capability report is invalid')
  failIf(!Array.isArray(report.packages) || report.packages.length !== Object.keys(packageContracts).length, 'capability package records are incomplete')
  const records = new Map()
  for (const record of report.packages) {
    failIf(!isRecord(record) || typeof record.name !== 'string' || !packageContracts[record.name] || records.has(record.name), 'capability package records are malformed or duplicated')
    records.set(record.name, record)
  }
  for (const [name, contract] of Object.entries(packageContracts)) validateRecord(records.get(name), contract, expectedVersion(contract, expectedVersions))
  const closureRecords = assertOfficialClosure(report.officialClosure)
  assertFeatureGroups(report.featureGroups, featureRecords(closureRecords, [...records.values()]))
  assertCapabilityRecords(report.capabilities, records)
  failIf(!sameBundles(report.profileBundles, PROFILE_BUNDLES), 'exact desktop profile bundles are unavailable')
  return report
}

function inspectOfficialClosure(runtimeRoot, expectedVersions, fs, pathImplementation) {
  const cliLocation = locatePackage(runtimeRoot, '@deepseek-ai/dsh', fs, pathImplementation)
  failIf(cliLocation.kind !== 'found', 'official CLI manifest is unavailable')
  let cliManifest
  try { cliManifest = JSON.parse(fs.readFileSync(cliLocation.packagePath, 'utf8')) } catch { failIf(true, 'official CLI manifest is invalid') }
  failIf(!isRecord(cliManifest.dependencies), 'official CLI dependencies are invalid')
  const dependencyEntries = Object.entries(cliManifest.dependencies)
  failIf(dependencyEntries.length === 0 || dependencyEntries.length > 512, 'official CLI dependencies are invalid')
  const packages = dependencyEntries
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([name, declaredRange]) => inspectClosurePackage(runtimeRoot, name, declaredRange, expectedVersions, fs, pathImplementation))
  return { digest: closureDigest(packages), packages }
}

function inspectClosurePackage(runtimeRoot, name, declaredRange, expectedVersions, fs, pathImplementation) {
  failIf(typeof name !== 'string' || !packageNamePattern.test(name), 'official CLI dependency name is invalid')
  failIf(typeof declaredRange !== 'string' || !isSupportedRange(declaredRange), 'official CLI dependency range is invalid')
  const location = locatePackage(runtimeRoot, name, fs, pathImplementation)
  if (location.kind === 'missing') return closureFailure(name, declaredRange, 'missing', 'MISSING_PACKAGE_JSON')
  if (location.kind === 'invalid') return closureFailure(name, declaredRange, 'incompatible', 'PACKAGE_PATH_INVALID')
  let manifest
  try { manifest = JSON.parse(fs.readFileSync(location.packagePath, 'utf8')) } catch { return closureFailure(name, declaredRange, 'incompatible', 'MANIFEST_INVALID') }
  const observedVersion = isVersion(manifest?.version) ? manifest.version : null
  const license = typeof manifest?.license === 'string' && manifest.license.length > 0 && manifest.license.length <= 128 ? manifest.license : null
  if (manifest?.name !== name) return closureFailure(name, declaredRange, 'incompatible', 'MANIFEST_NAME_INVALID', observedVersion, license)
  if (observedVersion === null || !versionSatisfies(observedVersion, declaredRange)) return closureFailure(name, declaredRange, 'incompatible', 'VERSION_MISMATCH', observedVersion, license)
  if (name.startsWith('@deepseek-ai/dsh-') && observedVersion !== expectedVersions.dshVersion) {
    return closureFailure(name, declaredRange, 'incompatible', 'VERSION_MISMATCH', observedVersion, license)
  }
  if (license === null) return closureFailure(name, declaredRange, 'incompatible', 'LICENSE_INVALID', observedVersion, null)
  const entrypoint = packageHasEntrypoint(location.directory, manifest, fs, pathImplementation)
  if (!entrypoint) return closureFailure(name, declaredRange, 'incompatible', 'ENTRYPOINT_INVALID', observedVersion, license)
  return { name, declaredRange, observedVersion, license, entrypoint: true, status: 'compatible' }
}

function packageHasEntrypoint(directory, manifest, fs, pathImplementation) {
  const candidates = []
  collectEntrypoints(manifest.exports, candidates)
  collectEntrypoints(manifest.main, candidates)
  collectEntrypoints(manifest.module, candidates)
  collectEntrypoints(manifest.bin, candidates)
  return [...new Set(candidates)]
    .filter((candidate) => candidate !== './package.json' && candidate !== 'package.json')
    .some((candidate) => isFileWithin(directory, candidate, { pathImplementation, ...fs }))
}

function collectEntrypoints(value, output) {
  if (typeof value === 'string') { output.push(value); return }
  if (Array.isArray(value)) { for (const item of value) collectEntrypoints(item, output); return }
  if (isRecord(value)) for (const item of Object.values(value)) collectEntrypoints(item, output)
}

function closureFailure(name, declaredRange, status, reasonCode, observedVersion = null, license = null) {
  return { name, declaredRange, observedVersion, license, entrypoint: false, status, reasonCode }
}

function closureDigest(packages) {
  return createHash('sha256').update(JSON.stringify(packages)).digest('hex')
}

function buildFeatureGroups(packages) {
  return Object.fromEntries(Object.entries(featureGroupMatchers).map(([feature, matches]) => {
    const owners = packages.filter((record) => matches(record.name)).map((record) => record.name)
    return [feature, { packages: owners, available: owners.length > 0 && owners.every((name) => packages.find((record) => record.name === name)?.status === 'compatible') }]
  }))
}

function featureRecords(closurePackages, fixedPackages) {
  const records = new Map(closurePackages.map((record) => [record.name, record]))
  for (const record of fixedPackages) {
    const existing = records.get(record.name)
    if (existing === undefined || record.status !== 'compatible') records.set(record.name, record)
  }
  return [...records.values()].sort((left, right) => left.name.localeCompare(right.name))
}

function assertOfficialClosure(closure) {
  failIf(!hasExactKeys(closure, ['digest', 'packages']) || typeof closure.digest !== 'string' || !/^[0-9a-f]{64}$/.test(closure.digest) || !Array.isArray(closure.packages), 'official dependency closure is invalid')
  failIf(closure.packages.length === 0 || closure.packages.length > 512 || closure.digest !== closureDigest(closure.packages), 'official dependency closure is invalid')
  let previous = ''
  for (const record of closure.packages) {
    failIf(!isRecord(record) || typeof record.name !== 'string' || record.name <= previous || typeof record.declaredRange !== 'string', 'official dependency closure is malformed or unsorted')
    previous = record.name
    if (record.status === 'compatible') {
      failIf(!hasExactKeys(record, ['declaredRange', 'entrypoint', 'license', 'name', 'observedVersion', 'status']) || record.entrypoint !== true || !isVersion(record.observedVersion) || typeof record.license !== 'string', 'official dependency closure is malformed')
    } else {
      failIf(!hasExactKeys(record, ['declaredRange', 'entrypoint', 'license', 'name', 'observedVersion', 'reasonCode', 'status']) || !['missing', 'incompatible'].includes(record.status) || record.entrypoint !== false || !CAPABILITY_REASON_CODES.includes(record.reasonCode), 'official dependency closure is malformed')
      failIf(true, 'official dependency closure is not compatible')
    }
  }
  for (const required of ['@deepseek-ai/dsh-base', '@deepseek-ai/dsh-web-app']) failIf(!closure.packages.some((record) => record.name === required), 'official dependency closure is incomplete')
  return closure.packages
}

function assertFeatureGroups(featureGroups, packages) {
  const expected = buildFeatureGroups(packages)
  failIf(!sameValue(featureGroups, expected), 'feature group records are invalid')
}

function isSupportedRange(value) {
  if (typeof value !== 'string' || value.length === 0 || value.length > 128) return false
  const candidate = value.startsWith('^') || value.startsWith('~') ? value.slice(1) : value
  return exactSemVer.test(candidate)
}

function versionSatisfies(version, range) {
  const operator = range[0] === '^' || range[0] === '~' ? range[0] : ''
  const base = operator ? range.slice(1) : range
  if (!exactSemVer.test(version) || !exactSemVer.test(base)) return false
  if (!operator) return version === base
  if (compareVersions(version, base) < 0) return false
  const parsed = parseVersion(base)
  const upper = operator === '~'
    ? `${parsed.major}.${parsed.minor + 1}.0`
    : parsed.major > 0
      ? `${parsed.major + 1}.0.0`
      : parsed.minor > 0
        ? `0.${parsed.minor + 1}.0`
        : `0.0.${parsed.patch + 1}`
  return compareVersions(version, upper) < 0
}

function compareVersions(left, right) {
  const a = parseVersion(left)
  const b = parseVersion(right)
  for (const field of ['major', 'minor', 'patch']) if (a[field] !== b[field]) return a[field] < b[field] ? -1 : 1
  if (a.prerelease.length === 0 || b.prerelease.length === 0) return a.prerelease.length === b.prerelease.length ? 0 : a.prerelease.length === 0 ? 1 : -1
  const length = Math.max(a.prerelease.length, b.prerelease.length)
  for (let index = 0; index < length; index += 1) {
    const aPart = a.prerelease[index]
    const bPart = b.prerelease[index]
    if (aPart === undefined || bPart === undefined) return aPart === undefined ? -1 : 1
    if (aPart === bPart) continue
    const aNumber = /^\d+$/.test(aPart)
    const bNumber = /^\d+$/.test(bPart)
    if (aNumber && bNumber) return Number(aPart) < Number(bPart) ? -1 : 1
    if (aNumber !== bNumber) return aNumber ? -1 : 1
    return aPart < bPart ? -1 : 1
  }
  return 0
}

function parseVersion(value) {
  const match = exactSemVer.exec(value)
  return { major: Number(match[1]), minor: Number(match[2]), patch: Number(match[3]), prerelease: match[4]?.split('.') ?? [] }
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
function sameValue(actual, expected) {
  if (actual === expected) return true
  if (Array.isArray(actual) || Array.isArray(expected)) {
    return Array.isArray(actual) && Array.isArray(expected) && actual.length === expected.length && actual.every((value, index) => sameValue(value, expected[index]))
  }
  if (!isRecord(actual) || !isRecord(expected)) return false
  const actualKeys = Object.keys(actual).sort()
  const expectedKeys = Object.keys(expected).sort()
  return actualKeys.length === expectedKeys.length && actualKeys.every((key, index) => key === expectedKeys[index] && sameValue(actual[key], expected[key]))
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : ''
if (invokedPath === resolve(fileURLToPath(import.meta.url))) {
  try { const root = process.argv.find((argument) => argument.startsWith('--runtime-root='))?.slice('--runtime-root='.length); if (!root) throw new Error('--runtime-root is required'); const { loadReleaseVersions } = await import('./release-versions.mjs'); const versions = loadReleaseVersions(); const desktopPluginVersion = JSON.parse(readFileSync(resolve('packages/dsh-plugin-desktop/package.json'), 'utf8')).version; process.stdout.write(`${JSON.stringify(inspectRuntimeCapabilities(resolve(root), { dshVersion: versions.dshVersion, desktopPluginVersion }), null, 2)}\n`) } catch (cause) { process.stderr.write(`${cause instanceof Error ? cause.message : String(cause)}\n`); process.exitCode = 1 }
}
