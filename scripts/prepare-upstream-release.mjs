import {
  existsSync,
  readFileSync,
  renameSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'
import {
  assertReleaseVersionConsistency,
  OFFICIAL_DSH_REPOSITORY,
  validateReleaseVersions,
} from './release-versions.mjs'

const NPM_LATEST_URL = 'https://registry.npmjs.org/@deepseek-ai%2Fdsh/latest'
const exactSemVer = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/

export function compareSemVer(left, right) {
  const a = parseSemVer(left)
  const b = parseSemVer(right)
  for (const field of ['major', 'minor', 'patch']) {
    if (a[field] !== b[field]) return a[field] < b[field] ? -1 : 1
  }
  if (a.prerelease.length === 0 || b.prerelease.length === 0) {
    if (a.prerelease.length === b.prerelease.length) return 0
    return a.prerelease.length === 0 ? 1 : -1
  }
  const length = Math.max(a.prerelease.length, b.prerelease.length)
  for (let index = 0; index < length; index += 1) {
    const aPart = a.prerelease[index]
    const bPart = b.prerelease[index]
    if (aPart === undefined || bPart === undefined) return aPart === undefined ? -1 : 1
    if (aPart === bPart) continue
    const aNumeric = /^\d+$/.test(aPart)
    const bNumeric = /^\d+$/.test(bPart)
    if (aNumeric && bNumeric) {
      if (aPart.length !== bPart.length) return aPart.length < bPart.length ? -1 : 1
      return aPart < bPart ? -1 : 1
    }
    if (aNumeric !== bNumeric) return aNumeric ? -1 : 1
    return aPart < bPart ? -1 : 1
  }
  return 0
}

export async function fetchLatestDshVersion(fetcher = globalThis.fetch) {
  if (typeof fetcher !== 'function') throw new Error('当前 Node.js 环境不支持 fetch')
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), 10_000)
  try {
    const response = await fetcher(NPM_LATEST_URL, {
      headers: { Accept: 'application/json' },
      redirect: 'error',
      signal: controller.signal,
    })
    if (!response.ok) throw new Error(`npm latest 查询失败: HTTP ${response.status}`)
    let payload
    try {
      payload = JSON.parse(await response.text())
    } catch (cause) {
      throw new Error(`npm latest 响应不是有效 JSON: ${errorMessage(cause)}`)
    }
    if (typeof payload?.version !== 'string') throw new Error('npm latest 响应缺少 version')
    parseSemVer(payload.version)
    return payload.version
  } finally {
    clearTimeout(timeout)
  }
}

export function parseDshTagRefs(output) {
  const tags = new Map()
  for (const line of String(output).split(/\r?\n/)) {
    const match = /^([0-9a-f]{40})\trefs\/tags\/(dsh-v([^\^]+))(\^\{\})?$/.exec(line)
    if (!match) continue
    try { parseSemVer(match[3]) } catch { continue }
    const current = tags.get(match[2]) ?? {}
    if (match[4]) current.peeled = match[1]
    else current.direct = match[1]
    tags.set(match[2], current)
  }
  const candidates = [...tags.entries()]
    .map(([tag, refs]) => ({ tag, version: tag.slice('dsh-v'.length), commit: refs.peeled ?? refs.direct }))
    .filter((candidate) => typeof candidate.commit === 'string')
    .sort((left, right) => compareSemVer(left.version, right.version))
  const latest = candidates.at(-1)
  if (!latest) throw new Error('未找到有效的官方 DSH tag')
  return { repository: OFFICIAL_DSH_REPOSITORY, ...latest }
}

export function fetchLatestDshSource(runner = spawnSync) {
  const result = runner('git', [
    'ls-remote', '--tags', OFFICIAL_DSH_REPOSITORY, 'refs/tags/dsh-v*',
  ], { encoding: 'utf8', timeout: 10_000, maxBuffer: 4 * 1024 * 1024 })
  if (result.status !== 0 || typeof result.stdout !== 'string') {
    throw new Error('官方 DSH tag 查询失败')
  }
  return parseDshTagRefs(result.stdout)
}

export async function prepareUpstreamRelease({ root = process.cwd(), latestVersion, latestSource }) {
  parseSemVer(latestVersion)
  const versions = assertReleaseVersionConsistency(root)
  const validatedSource = validateReleaseVersions({ ...versions, dshUpstream: latestSource }).dshUpstream
  const comparison = compareSemVer(versions.dshVersion, latestVersion)
  if (comparison > 0) {
    throw new Error(`拒绝将 DSH 从 ${versions.dshVersion} 降级到 ${latestVersion}`)
  }
  const currentSourceVersion = versions.dshUpstream.tag.slice('dsh-v'.length)
  const latestSourceVersion = validatedSource.tag.slice('dsh-v'.length)
  const sourceComparison = compareSemVer(currentSourceVersion, latestSourceVersion)
  if (sourceComparison > 0) {
    throw new Error(`拒绝将 DSH source 从 ${versions.dshUpstream.tag} 降级到 ${validatedSource.tag}`)
  }
  if (sourceComparison === 0 && versions.dshUpstream.commit !== validatedSource.commit) {
    throw new Error(`官方 DSH tag ${validatedSource.tag} 的 commit 发生变化，拒绝静默改写`)
  }
  const distributionUpgrade = comparison < 0
  const sourceUpdate = sourceComparison < 0
  if (!distributionUpgrade && !sourceUpdate) return resultFor('noop', versions, versions)

  const nextVersions = validateReleaseVersions({
    ...versions,
    desktopVersion: distributionUpgrade ? bumpStablePatch(versions.desktopVersion) : versions.desktopVersion,
    runtimeVersion: distributionUpgrade ? bumpPreviewPatch(versions.runtimeVersion) : versions.runtimeVersion,
    dshVersion: distributionUpgrade ? latestVersion : versions.dshVersion,
    dshUpstream: validatedSource,
  })
  const updates = buildUpdates(root, nextVersions, { includeDerivedVersions: distributionUpgrade })
  commitUpdatesAtomically(root, updates)
  try {
    assertReleaseVersionConsistency(root)
  } catch (cause) {
    restoreOriginals(updates)
    throw cause
  }
  return resultFor(distributionUpgrade ? 'upgrade' : 'source-update', nextVersions, versions)
}

function buildUpdates(root, versions, { includeDerivedVersions }) {
  const updates = []
  const addJson = (relativePath, mutate) => {
    const original = readFile(root, relativePath)
    const parsed = JSON.parse(original)
    const next = mutate(parsed)
    updates.push(update(root, relativePath, original, `${JSON.stringify(next, null, 2)}\n`))
  }

  addJson('release/versions.json', () => versions)
  if (!includeDerivedVersions) return updates
  addJson('package.json', (value) => ({ ...value, version: versions.desktopVersion }))
  addJson('package-lock.json', (value) => ({
    ...value,
    version: versions.desktopVersion,
    packages: {
      ...value.packages,
      '': { ...value.packages?.[''], version: versions.desktopVersion },
    },
  }))
  addJson('src-tauri/tauri.conf.json', (value) => ({ ...value, version: versions.desktopVersion }))

  const cargoManifestPath = 'src-tauri/Cargo.toml'
  const cargoManifest = readFile(root, cargoManifestPath)
  updates.push(update(root, cargoManifestPath, cargoManifest, replacePackageVersion(
    cargoManifest,
    'deepseek-harness-desktop',
    versions.desktopVersion,
    cargoManifestPath,
  )))

  const cargoLockPath = 'src-tauri/Cargo.lock'
  const cargoLock = readFile(root, cargoLockPath)
  updates.push(update(root, cargoLockPath, cargoLock, replacePackageVersion(
    cargoLock,
    'deepseek-harness-desktop',
    versions.desktopVersion,
    cargoLockPath,
  )))
  return updates
}

function commitUpdatesAtomically(root, updates) {
  const suffix = `.release-update-${process.pid}-${Date.now()}`
  const staged = []
  try {
    for (const item of updates) {
      const path = `${item.path}${suffix}`
      writeFileSync(path, item.next, { flag: 'wx' })
      staged.push(path)
    }
    for (let index = 0; index < updates.length; index += 1) {
      renameSync(staged[index], updates[index].path)
    }
  } catch (cause) {
    for (const path of staged) if (existsSync(path)) unlinkSync(path)
    restoreOriginals(updates)
    throw new Error(`更新 release versions 失败，已尝试恢复原文件: ${errorMessage(cause)}`)
  }
  void root
}

function restoreOriginals(updates) {
  for (const item of updates) writeFileSync(item.path, item.original)
}

function update(root, relativePath, original, next) {
  return { path: resolve(root, relativePath), original, next }
}

function readFile(root, relativePath) {
  return readFileSync(resolve(root, relativePath), 'utf8')
}

function replacePackageVersion(source, packageName, version, path) {
  const packagePattern = new RegExp(`(^|\\n)(\\[\\[?package\\]?\\][\\s\\S]*?)(?=\\n\\[|$)`, 'g')
  let replaced = false
  const next = source.replace(packagePattern, (block) => {
    if (replaced || !new RegExp(`^name\\s*=\\s*"${packageName}"`, 'm').test(block)) return block
    const matches = block.match(/^version\s*=\s*"[^"]+"/gm) ?? []
    if (matches.length !== 1) throw new Error(`${path} 中 ${packageName} 的 version 数量不是 1`)
    replaced = true
    return block.replace(/^version\s*=\s*"[^"]+"/m, `version = "${version}"`)
  })
  if (!replaced) throw new Error(`${path} 中找不到 ${packageName} package`)
  return next
}

function parseSemVer(version) {
  if (typeof version !== 'string' || version.trim() !== version) throw new Error(`不是精确 SemVer: ${String(version)}`)
  const match = exactSemVer.exec(version)
  if (!match) throw new Error(`不是精确 SemVer: ${version}`)
  return {
    major: Number(match[1]),
    minor: Number(match[2]),
    patch: Number(match[3]),
    prerelease: match[4]?.split('.') ?? [],
  }
}

function bumpStablePatch(version) {
  const parsed = parseSemVer(version)
  if (parsed.prerelease.length > 0 || version.includes('+')) throw new Error(`桌面版本必须是稳定 SemVer: ${version}`)
  return `${parsed.major}.${parsed.minor}.${parsed.patch + 1}`
}

function bumpPreviewPatch(version) {
  const match = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)-preview$/.exec(version)
  if (!match) throw new Error(`Runtime 版本必须是 X.Y.Z-preview: ${version}`)
  return `${match[1]}.${match[2]}.${Number(match[3]) + 1}-preview`
}

function resultFor(action, versions, previousVersions) {
  return {
    action,
    previousDshVersion: previousVersions.dshVersion,
    dshVersion: versions.dshVersion,
    desktopVersion: versions.desktopVersion,
    runtimeVersion: versions.runtimeVersion,
    tag: `desktop-v${versions.desktopVersion}`,
    previousUpstreamTag: previousVersions.dshUpstream.tag,
    upstreamTag: versions.dshUpstream.tag,
    upstreamCommit: versions.dshUpstream.commit,
  }
}

function errorMessage(cause) {
  return cause instanceof Error ? cause.message : String(cause)
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : ''
if (invokedPath === resolve(fileURLToPath(import.meta.url))) {
  try {
    const options = process.argv.slice(2)
    if (options.some((option) => !/^--(?:latest|source-tag|source-commit)=/.test(option))) {
      throw new Error('只支持 --latest、--source-tag 和 --source-commit')
    }
    const values = Object.fromEntries(options.map((option) => {
      const [key, ...rest] = option.slice(2).split('=')
      return [key, rest.join('=')]
    }))
    if ((values['source-tag'] === undefined) !== (values['source-commit'] === undefined)) {
      throw new Error('--source-tag 和 --source-commit 必须同时提供')
    }
    const latestVersion = values.latest ?? await fetchLatestDshVersion()
    const latestSource = values['source-tag'] === undefined
      ? fetchLatestDshSource()
      : {
          repository: OFFICIAL_DSH_REPOSITORY,
          tag: values['source-tag'],
          commit: values['source-commit'],
        }
    process.stdout.write(`${JSON.stringify(await prepareUpstreamRelease({ latestVersion, latestSource }), null, 2)}\n`)
  } catch (cause) {
    process.stderr.write(`${errorMessage(cause)}\n`)
    process.exitCode = 1
  }
}
