import { createHash } from 'node:crypto'
import {
  lstatSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  writeFileSync,
} from 'node:fs'
import { basename, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { loadReleaseVersions, validateReleaseVersions } from './release-versions.mjs'

export const productionRepository = 'XingAur/deepseek-harness-desktop'
const safeAssetName = /^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._ -]*$/

export function verifyDesktopReleaseAssets({ assetDirectory, versions }) {
  const releaseVersions = validateReleaseVersions(versions)
  const directory = resolveRequiredPath(assetDirectory, 'assetDirectory')
  const escapedVersion = escapeRegExp(releaseVersions.desktopVersion)
  const patterns = {
    windowsInstaller: new RegExp(`_${escapedVersion}_x64-setup\\.exe$`),
    windowsSignature: new RegExp(`_${escapedVersion}_x64-setup\\.exe\\.sig$`),
    macDmg: new RegExp(`_${escapedVersion}_aarch64\\.dmg$`),
  }
  const releaseLike = readdirSync(directory, { withFileTypes: true })
    .filter((entry) => /(?:\.exe|\.exe\.sig|\.dmg|\.nsis\.zip|\.nsis\.zip\.sig)$/.test(entry.name))

  for (const entry of releaseLike) {
    if (!entry.isFile() || !safeAssetName.test(entry.name)) {
      throw new Error(`不安全的发布资产: ${entry.name}`)
    }
  }

  const selected = Object.fromEntries(Object.entries(patterns).map(([kind, pattern]) => {
    const matches = releaseLike.filter((entry) => pattern.test(entry.name))
    if (matches.length !== 1) throw new Error(`${kind} 资产数量必须是 1，实际 ${matches.length}`)
    return [kind, matches[0].name]
  }))
  const selectedNames = new Set(Object.values(selected))
  const unexpected = releaseLike.filter((entry) => !selectedNames.has(entry.name)).map((entry) => entry.name)
  if (unexpected.length > 0) throw new Error(`发现错误版本、重复或不支持平台的发布资产: ${unexpected.join(', ')}`)
  if (selected.windowsSignature !== `${selected.windowsInstaller}.sig`) {
    throw new Error('Windows 更新签名文件名必须与 NSIS 安装器完全匹配')
  }

  const paths = Object.fromEntries(Object.entries(selected).map(([kind, name]) => {
    const path = resolve(directory, name)
    const stat = lstatSync(path)
    if (!stat.isFile() || stat.isSymbolicLink() || stat.size === 0) throw new Error(`${kind} 资产必须是非空普通文件`)
    return [`${kind}Path`, path]
  }))
  const signature = readFileSync(paths.windowsSignaturePath, 'utf8').trim()
  if (signature.length === 0 || /\r|\n/.test(signature)) throw new Error('Windows 更新签名必须是非空单行文本')

  return {
    ...selected,
    ...Object.fromEntries(Object.entries(selected).map(([kind, name]) => [`${kind}Name`, name])),
    ...paths,
    signature,
  }
}

export function generateDesktopRelease({
  assetDirectory,
  outputDirectory,
  repository,
  publishedAt,
  notes,
  versions,
}) {
  if (repository !== productionRepository) throw new Error(`发布元数据必须使用固定仓库 ${productionRepository}`)
  if (typeof notes !== 'string') throw new Error('notes 必须是字符串')
  if (typeof publishedAt !== 'string' || !isCanonicalTimestamp(publishedAt)) {
    throw new Error('publishedAt 必须是 UTC ISO-8601 时间')
  }
  const releaseVersions = validateReleaseVersions(versions)
  const assets = verifyDesktopReleaseAssets({ assetDirectory, versions: releaseVersions })
  const output = resolveRequiredPath(outputDirectory, 'outputDirectory')
  mkdirSync(output, { recursive: true })

  const tag = `desktop-v${releaseVersions.desktopVersion}`
  const releaseBaseUrl = `https://github.com/${productionRepository}/releases/download/${tag}`
  const releasePageUrl = `https://github.com/${productionRepository}/releases/tag/${tag}`
  const assetUrl = (name) => `${releaseBaseUrl}/${encodeURIComponent(name)}`
  const windowsUpdater = fileFacts(assets.windowsInstallerPath)
  const macDmg = fileFacts(assets.macDmgPath)

  const latest = {
    version: releaseVersions.desktopVersion,
    notes,
    pub_date: publishedAt,
    platforms: {
      'windows-x86_64': {
        signature: assets.signature,
        url: assetUrl(assets.windowsInstallerName),
      },
    },
  }
  const manifest = {
    schemaVersion: 1,
    version: releaseVersions.desktopVersion,
    tag,
    publishedAt,
    notes,
    releasePageUrl,
    platforms: {
      'windows-x86_64': {
        mode: 'in-app',
        url: assetUrl(assets.windowsInstallerName),
        signatureUrl: assetUrl(assets.windowsSignatureName),
        sha256: windowsUpdater.sha256,
        size: windowsUpdater.size,
      },
      'darwin-aarch64': {
        mode: 'manual-dmg',
        url: assetUrl(assets.macDmgName),
        sha256: macDmg.sha256,
        size: macDmg.size,
        developerIdSigned: false,
        notarized: false,
      },
    },
  }

  const latestPath = resolve(output, 'latest.json')
  const manifestPath = resolve(output, 'desktop-release.json')
  writeJson(latestPath, latest)
  writeJson(manifestPath, manifest)
  return {
    latestPath,
    manifestPath,
    uploadableAssets: [
      assets.windowsInstallerPath,
      assets.windowsSignaturePath,
      assets.macDmgPath,
      latestPath,
      manifestPath,
    ],
  }
}

function fileFacts(path) {
  const bytes = readFileSync(path)
  return {
    sha256: createHash('sha256').update(bytes).digest('hex'),
    size: bytes.length,
  }
}

function resolveRequiredPath(value, label) {
  if (typeof value !== 'string' || value.trim() === '') throw new Error(`${label} 不能为空`)
  return resolve(value)
}

function writeJson(path, value) {
  writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`, 'utf8')
}

function isCanonicalTimestamp(value) {
  const parsed = new Date(value)
  return !Number.isNaN(parsed.valueOf()) && parsed.toISOString() === value
}

function escapeRegExp(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function option(name, fallback) {
  const prefix = `--${name}=`
  const value = process.argv.slice(2).find((candidate) => candidate.startsWith(prefix))
  return value ? value.slice(prefix.length) : fallback
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : ''
if (invokedPath === resolve(fileURLToPath(import.meta.url))) {
  try {
    const outputDirectory = option('output', 'release-output')
    const result = generateDesktopRelease({
      assetDirectory: option('assets', 'release-assets'),
      outputDirectory,
      repository: option('repository', process.env.GITHUB_REPOSITORY ?? productionRepository),
      publishedAt: option('published-at', new Date().toISOString()),
      notes: option('notes', 'Automated upstream release.'),
      versions: loadReleaseVersions(),
    })
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`)
  } catch (cause) {
    process.stderr.write(`${cause instanceof Error ? cause.message : String(cause)}\n`)
    process.exitCode = 1
  }
}
