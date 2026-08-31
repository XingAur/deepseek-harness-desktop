#!/usr/bin/env node
/**
 * 把本机 Harness Core 源码 vendor 进桌面仓库（vendor/harness-core）。
 *
 * 这是发布链路的第一步：Harness Core 仓库本身不在 git 里，安装包与 CI
 * 构建必须能从桌面仓库内部拿到一份确定性的 Core 副本。脚本只拷贝代码
 * 与配置（排除运行数据、虚拟环境、缓存），并对 JSON/配置类文件做密钥
 * 样式扫描——发现真实凭证样式立即失败，绝不静默带入仓库。
 *
 * 用法：
 *   node scripts/vendor-harness-core.mjs --source /Users/lym/WorkCode/ai/Harness
 */

import { createHash } from 'node:crypto'
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const HARNESS_CORE_VENDOR_DIRS = ['app', 'tools', 'prompts', 'config', 'skills', 'tests', 'fixtures', 'harnesses']
export const HARNESS_CORE_VENDOR_FILES = [
  'requirements.txt',
  'run.py',
  'install_manifest.json',
  'README.md',
  'HANDOFF.md',
]
export const VENDOR_MANIFEST_NAME = 'VENDOR_MANIFEST.json'

const EXCLUDED_PATH_PATTERNS = [
  /(^|\/)__pycache__(\/|$)/,
  /(^|\/)\.venv(\/|$)/,
  /(^|\/)\.git(\/|$)/,
  /(^|\/)\.DS_Store$/,
  /(^|\/)data(\/|$)/,
  /(^|\/)runs(\/|$)/,
  /\.pyc$/,
  /\.pyo$/,
  /(^|\/)\.tmp(\/|$)/,
]

/** 凭证赋值样式：json/env/toml 等数据文件里命中即失败；.py 里通常是凭证读取代码，仅提示。 */
const SECRET_ASSIGNMENT = /(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|private[_-]?key)["']?\s*[:=]\s*["'][A-Za-z0-9_\-+/=]{16,}["']/i
/** 占位符样式（模板/example 文件里的引用写法）不算真实凭证。 */
const PLACEHOLDER_VALUE = /(ref|placeholder|your|example|fill|replace|<[^>]*>|\{\{|xxx|todo|change[_-]?me|sentinel|self[_-]?check)/i
const SECRET_TEXT_FILES = /\.(json|jsonc|toml|ya?ml|env|ini|cfg|conf)$/i

export function isSecretAssignment(text) {
  const match = SECRET_ASSIGNMENT.exec(text)
  if (match === null) return false
  return !PLACEHOLDER_VALUE.test(match[0])
}

export function isVendorablePath(relativePath) {
  return !EXCLUDED_PATH_PATTERNS.some((pattern) => pattern.test(relativePath))
}

/** 拷贝 vendor 清单内的 Core 内容到目标目录，返回清单统计。preserve 里的顶层条目（如已安装的 Python 运行时）不会被清空。 */
export function copyHarnessCore(source, target, { preserve = [] } = []) {
  const sourceRoot = resolve(source)
  const targetRoot = resolve(target)
  if (!existsSync(sourceRoot)) throw new Error(`Harness Core 源目录不存在：${sourceRoot}`)
  if (existsSync(targetRoot)) {
    for (const entry of readdirSync(targetRoot)) {
      if (!preserve.includes(entry)) rmSync(join(targetRoot, entry), { recursive: true, force: true })
    }
  }
  mkdirSync(targetRoot, { recursive: true })
  const files = []
  const collect = (absolute, base) => {
    for (const entry of readdirSync(absolute, { withFileTypes: true })) {
      const entryPath = join(absolute, entry.name)
      const relativePath = base === '' ? entry.name : `${base}/${entry.name}`
      if (!isVendorablePath(relativePath)) continue
      if (entry.isDirectory()) collect(entryPath, relativePath)
      else if (entry.isFile()) files.push({ absolute: entryPath, relativePath })
    }
  }
  for (const name of HARNESS_CORE_VENDOR_DIRS) {
    const directory = join(sourceRoot, name)
    if (existsSync(directory)) collect(directory, name)
  }
  for (const name of HARNESS_CORE_VENDOR_FILES) {
    const file = join(sourceRoot, name)
    if (existsSync(file)) files.push({ absolute: file, relativePath: name })
  }
  for (const file of files) {
    const destination = join(targetRoot, file.relativePath)
    mkdirSync(dirname(destination), { recursive: true })
    cpSync(file.absolute, destination)
  }
  return {
    fileCount: files.length,
    totalBytes: files.reduce((sum, file) => sum + statSync(file.absolute).size, 0),
    manifestSha256: sha256OfEntries(
      files.map((file) => `${file.relativePath}:${sha256OfFile(file.absolute)}`),
    ),
  }
}

function sha256OfEntries(entries) {
  return createHash('sha256').update(entries.join('\n')).digest('hex')
}

function sha256OfFile(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex')
}

/**
 * 扫描已拷贝目录中的数据类文件是否带凭证样式赋值。
 * 返回告警列表（.py 命中）；数据文件命中直接抛错。
 */
export function verifyVendorNoSecrets(target) {
  const warnings = []
  const walk = (directory) => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const entryPath = join(directory, entry.name)
      const relativePath = relative(resolve(target), entryPath)
      if (entry.isDirectory()) {
        walk(entryPath)
        continue
      }
      if (!entry.isFile() || statSync(entryPath).size > 2 * 1024 * 1024) continue
      const text = readFileSync(entryPath, 'utf8')
      if (!isSecretAssignment(text)) continue
      if (SECRET_TEXT_FILES.test(entry.name)) {
        throw new Error(`vendor 目录中的 ${entry.name} 包含凭证样式赋值，禁止带入仓库：${relativePath}`)
      }
      warnings.push(relativePath)
    }
  }
  walk(resolve(target))
  return warnings
}

export function writeVendorManifest(target, { source, fileCount, totalBytes, manifestSha256 }) {
  const manifest = {
    schema: 'harness-core-vendor.v1',
    source: resolve(source),
    syncedAt: new Date().toISOString(),
    fileCount,
    totalBytes,
    manifestSha256,
  }
  writeFileSync(join(resolve(target), VENDOR_MANIFEST_NAME), `${JSON.stringify(manifest, null, 2)}\n`)
  return manifest
}

/** 源目录解析：显式指定时只认显式值；否则回退 env > 上次记录路径 > 常用默认路径。 */
export function resolveHarnessCoreSource(repositoryRoot, explicit) {
  if (typeof explicit === 'string' && explicit.trim() !== '') {
    const resolved = resolve(explicit)
    return isHarnessCoreSource(resolved) ? resolved : ''
  }
  const candidates = [
    process.env.HARNESS_CORE_SOURCE,
    readRecordedSource(join(repositoryRoot, 'vendor', 'harness-core')),
    '/Users/lym/WorkCode/ai/Harness',
  ].filter((value) => typeof value === 'string' && value.trim() !== '')
  for (const candidate of candidates) {
    const resolved = resolve(candidate)
    if (isHarnessCoreSource(resolved)) return resolved
  }
  return ''
}

function readRecordedSource(vendor) {
  const manifestPath = join(vendor, VENDOR_MANIFEST_NAME)
  if (!existsSync(manifestPath)) return undefined
  try {
    const value = JSON.parse(readFileSync(manifestPath, 'utf8'))
    return typeof value.source === 'string' ? value.source : undefined
  } catch {
    return undefined
  }
}

function isHarnessCoreSource(directory) {
  return existsSync(join(directory, 'app'))
    && existsSync(join(directory, 'tools', 'harness_host_server.py'))
    && existsSync(join(directory, 'requirements.txt'))
}

/**
 * 构建时自动同步：本机存在 Harness 源目录时，把最新源码 vendor 进仓库，
 * 用户无需记忆任何命令。CI（CI=true）或源目录不存在时跳过，使用仓库内
 * vendor 副本。密钥扫描门禁照常生效：源码里出现真实凭证样式直接让构建失败。
 */
export function syncVendorFromSource(repositoryRoot, { source } = {}) {
  if (process.env.DSH_HARNESS_VENDOR_SYNC === '0' || process.env.CI === 'true') {
    return { synced: false, changed: false, source: '', reason: 'disabled' }
  }
  const target = join(repositoryRoot, 'vendor', 'harness-core')
  const resolved = resolveHarnessCoreSource(repositoryRoot, source)
  if (resolved === '') {
    return { synced: false, changed: false, source: '', reason: 'source-unavailable' }
  }
  if (resolved === resolve(target)) {
    return { synced: false, changed: false, source: resolved, reason: 'source-is-vendor' }
  }
  const before = readRecordedManifestSha(target)
  const copied = copyHarnessCore(resolved, target)
  const warnings = verifyVendorNoSecrets(target)
  writeVendorManifest(target, { source: resolved, ...copied })
  return {
    synced: true,
    changed: copied.manifestSha256 !== before,
    source: resolved,
    fileCount: copied.fileCount,
    warnings,
  }
}

function readRecordedManifestSha(target) {
  const manifestPath = join(target, VENDOR_MANIFEST_NAME)
  if (!existsSync(manifestPath)) return ''
  try {
    const value = JSON.parse(readFileSync(manifestPath, 'utf8'))
    return typeof value.manifestSha256 === 'string' ? value.manifestSha256 : ''
  } catch {
    return ''
  }
}

function main() {
  const args = process.argv.slice(2)
  const sourceIndex = args.indexOf('--source')
  const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
  const target = join(repositoryRoot, 'vendor', 'harness-core')
  const source = resolveHarnessCoreSource(
    repositoryRoot,
    sourceIndex >= 0 ? args[sourceIndex + 1] : undefined,
  )
  if (source === '') {
    throw new Error('未找到可用的 Harness Core 源目录：用 --source 指定，或设置 HARNESS_CORE_SOURCE')
  }
  const copied = copyHarnessCore(source, target)
  const warnings = verifyVendorNoSecrets(target)
  const manifest = writeVendorManifest(target, { source, ...copied })
  process.stdout.write(
    `已 vendor Harness Core：${copied.fileCount} 个文件，${(copied.totalBytes / 1024 / 1024).toFixed(1)}MB → vendor/harness-core\n`,
  )
  if (warnings.length > 0) {
    process.stdout.write(`提示：以下 .py 文件包含凭证读取样式（非真实凭证），已放行：\n${warnings.join('\n')}\n`)
  }
  process.stdout.write(`${JSON.stringify(manifest, null, 2)}\n`)
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main()
}
