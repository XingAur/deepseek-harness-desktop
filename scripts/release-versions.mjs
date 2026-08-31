import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const RELEASE_VERSIONS_PATH = 'release/versions.json'
export const LEGACY_RELEASE_BASELINE = '0.1.12'

const exactKeys = [
  'schemaVersion',
  'desktopVersion',
  'runtimeVersion',
  'dshVersion',
  'dshUpstream',
  'nodeVersion',
  'pnpmVersion',
  'legacyReleaseBaseline',
].sort()
const exactUpstreamKeys = ['repository', 'tag', 'commit'].sort()
export const OFFICIAL_DSH_REPOSITORY = 'https://github.com/deepseek-ai/deepseek-harness.git'
const stableVersion = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$/
const exactSemVer = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/
const previewVersion = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)-preview$/

export function validateReleaseVersions(value) {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw invalid('必须是 JSON 对象')
  }
  const keys = Object.keys(value).sort()
  if (keys.length !== exactKeys.length || keys.some((key, index) => key !== exactKeys[index])) {
    throw invalid(`字段必须且只能是 ${exactKeys.join(', ')}`)
  }
  if (value.schemaVersion !== 2) throw invalid('schemaVersion 必须是 2')
  for (const field of ['desktopVersion', 'runtimeVersion', 'dshVersion', 'nodeVersion', 'pnpmVersion', 'legacyReleaseBaseline']) {
    if (typeof value[field] !== 'string' || value[field].trim() !== value[field]) {
      throw invalid(`${field} 必须是无首尾空格的字符串`)
    }
  }
  if (!stableVersion.test(value.desktopVersion)) throw invalid('desktopVersion 必须是三段稳定 SemVer')
  if (!previewVersion.test(value.runtimeVersion)) throw invalid('runtimeVersion 必须是 X.Y.Z-preview')
  if (!exactSemVer.test(value.dshVersion)) throw invalid('dshVersion 必须是精确 SemVer')
  validateDshUpstream(value.dshUpstream)
  if (!stableVersion.test(value.nodeVersion)) throw invalid('nodeVersion 必须是三段稳定 SemVer')
  if (!stableVersion.test(value.pnpmVersion)) throw invalid('pnpmVersion 必须是三段稳定 SemVer')
  if (value.legacyReleaseBaseline !== LEGACY_RELEASE_BASELINE) {
    throw invalid(`legacyReleaseBaseline 必须固定为 ${LEGACY_RELEASE_BASELINE}`)
  }
  return { ...value, dshUpstream: { ...value.dshUpstream } }
}

function validateDshUpstream(value) {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    throw invalid('dshUpstream 必须是 JSON 对象')
  }
  const keys = Object.keys(value).sort()
  if (keys.length !== exactUpstreamKeys.length || keys.some((key, index) => key !== exactUpstreamKeys[index])) {
    throw invalid(`dshUpstream 字段必须且只能是 ${exactUpstreamKeys.join(', ')}`)
  }
  if (value.repository !== OFFICIAL_DSH_REPOSITORY) throw invalid(`dshUpstream.repository 必须是 ${OFFICIAL_DSH_REPOSITORY}`)
  if (typeof value.tag !== 'string' || !value.tag.startsWith('dsh-v') || !exactSemVer.test(value.tag.slice('dsh-v'.length))) {
    throw invalid('dshUpstream.tag 必须是 dsh-v<精确 SemVer>')
  }
  if (typeof value.commit !== 'string' || !/^[0-9a-f]{40}$/.test(value.commit)) {
    throw invalid('dshUpstream.commit 必须是 40 位小写 Git 对象 ID')
  }
}

export function loadReleaseVersions(root = process.cwd()) {
  const path = resolve(root, RELEASE_VERSIONS_PATH)
  let value
  try {
    value = JSON.parse(readFileSync(path, 'utf8'))
  } catch (cause) {
    throw invalid(`无法读取 ${RELEASE_VERSIONS_PATH}: ${cause instanceof Error ? cause.message : String(cause)}`)
  }
  return validateReleaseVersions(value)
}

export function assertReleaseVersionConsistency(root = process.cwd()) {
  const versions = loadReleaseVersions(root)
  const mismatches = []
  const packageJson = readJson(root, 'package.json')
  const packageLock = readJson(root, 'package-lock.json')
  const tauriConfig = readJson(root, 'src-tauri/tauri.conf.json')
  const cargoManifest = readText(root, 'src-tauri/Cargo.toml')
  const cargoLock = readText(root, 'src-tauri/Cargo.lock')
  const runtimeBuilder = readText(root, 'scripts/build-runtime.mjs')
  const windowsInstaller = readText(root, 'scripts/windows-installer.mjs')

  compare(mismatches, 'package.json version', packageJson.version, versions.desktopVersion)
  compare(mismatches, 'package-lock.json version', packageLock.version, versions.desktopVersion)
  compare(mismatches, 'package-lock.json root package version', packageLock.packages?.['']?.version, versions.desktopVersion)
  compare(mismatches, 'src-tauri/tauri.conf.json version', tauriConfig.version, versions.desktopVersion)
  compare(mismatches, 'src-tauri/Cargo.toml package version', cargoPackageVersion(cargoManifest), versions.desktopVersion)
  compare(mismatches, 'src-tauri/Cargo.lock package version', cargoLockVersion(cargoLock), versions.desktopVersion)
  requireSource(mismatches, 'scripts/build-runtime.mjs', runtimeBuilder, [
    "from './release-versions.mjs'",
    'versions.nodeVersion',
    'versions.dshVersion',
    'versions.pnpmVersion',
  ])
  requireSource(mismatches, 'scripts/windows-installer.mjs', windowsInstaller, [
    "from './release-versions.mjs'",
    'loadReleaseVersions().runtimeVersion',
  ])

  if (mismatches.length > 0) {
    throw new Error(`release versions 不一致:\n- ${mismatches.join('\n- ')}`)
  }
  return versions
}

function invalid(message) {
  return new Error(`release versions 无效: ${message}`)
}

function readJson(root, path) {
  try {
    return JSON.parse(readText(root, path))
  } catch (cause) {
    throw new Error(`release versions 无法解析 ${path}: ${cause instanceof Error ? cause.message : String(cause)}`)
  }
}

function readText(root, path) {
  return readFileSync(resolve(root, path), 'utf8')
}

function compare(mismatches, label, actual, expected) {
  if (actual !== expected) mismatches.push(`${label}: ${String(actual)}，期望 ${expected}`)
}

function requireSource(mismatches, path, source, fragments) {
  for (const fragment of fragments) {
    if (!source.includes(fragment)) mismatches.push(`${path} 缺少版本源引用 ${fragment}`)
  }
}

function cargoPackageVersion(source) {
  return source.match(/^\[package\][\s\S]*?^version\s*=\s*"([^"]+)"/m)?.[1]
}

function cargoLockVersion(source) {
  const packages = source.split('[[package]]')
  const block = packages.find((candidate) => /^\s*name\s*=\s*"deepseek-harness-desktop"/m.test(candidate))
  return block?.match(/^version\s*=\s*"([^"]+)"/m)?.[1]
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : ''
if (invokedPath === resolve(fileURLToPath(import.meta.url))) {
  try {
    if (process.argv[2] !== '--check') throw new Error('只支持 --check')
    process.stdout.write(`${JSON.stringify(assertReleaseVersionConsistency(), null, 2)}\n`)
  } catch (cause) {
    process.stderr.write(`${cause instanceof Error ? cause.message : String(cause)}\n`)
    process.exitCode = 1
  }
}
