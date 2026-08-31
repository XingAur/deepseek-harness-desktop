#!/usr/bin/env node

import { createHash } from 'node:crypto'
import {
  cpSync,
  existsSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, relative, resolve, sep } from 'node:path'

export const HARNESS_PLUGIN_VENDOR_MANIFEST = 'VENDOR_MANIFEST.json'
const RELOCATABLE_HIS_HARNESS_CORE_ROOT_PATCH = 'relocatable-his-harness-core-root'
const HIS_HARNESS_CORE_ROOT_OLD =
  '_STAGED_HARNESS_ROOT = Path(__file__).resolve().parents[3] / "Harness"'
const HIS_HARNESS_CORE_ROOT_CURRENT = [
  '_BUNDLED_HARNESS_ROOT = Path(__file__).resolve().parents[3] / "core"',
  '_DESKTOP_BUILD_HARNESS_ROOT = Path(__file__).resolve().parents[3] / "harness-core"',
  HIS_HARNESS_CORE_ROOT_OLD,
].join('\n')
const HIS_HARNESS_CORE_CANDIDATES_OLD = [
  '        _STAGED_HARNESS_ROOT,',
  '        _DOCUMENTED_HARNESS_ROOT,',
].join('\n')
const HIS_HARNESS_CORE_CANDIDATES_CURRENT = [
  '        _BUNDLED_HARNESS_ROOT,',
  '        _DESKTOP_BUILD_HARNESS_ROOT,',
  '        _STAGED_HARNESS_ROOT,',
  '        _DOCUMENTED_HARNESS_ROOT,',
].join('\n')

const EXCLUDED = [
  /(^|\/)__pycache__(\/|$)/,
  /(^|\/)tests?(\/|$)/,
  /(^|\/)\.git(\/|$)/,
  /(^|\/)\.DS_Store$/,
  /\.py[co]$/,
]

function sha256(value) {
  return createHash('sha256').update(value).digest('hex')
}

function sha256File(path) {
  return sha256(readFileSync(path))
}

function inventory(path) {
  const payload = JSON.parse(readFileSync(resolve(path), 'utf8'))
  if (payload?.schema_version !== 'his-plugin-inventory.v1' || !Array.isArray(payload.plugins)) {
    throw new Error('Harness 插件冻结清单无效')
  }
  return payload.plugins
}

function safeRelativePath(path) {
  return typeof path === 'string'
    && path !== ''
    && !path.startsWith('/')
    && !path.includes('\\')
    && !path.split('/').some((part) => part === '' || part === '.' || part === '..')
}

function assertInside(root, path) {
  const normalizedRoot = `${resolve(root)}${sep}`
  if (!resolve(path).startsWith(normalizedRoot)) throw new Error('Harness 插件路径越界')
}

function verifyPlugin(root, item) {
  const pluginRoot = resolve(root, item.name)
  assertInside(root, pluginRoot)
  const descriptorPath = join(pluginRoot, '.codex-plugin', 'plugin.json')
  const capabilitiesPath = join(pluginRoot, 'capabilities.json')
  if (!existsSync(descriptorPath) || !existsSync(capabilitiesPath)) {
    throw new Error(`Harness 插件不完整：${item.name}`)
  }
  const descriptor = JSON.parse(readFileSync(descriptorPath, 'utf8'))
  const capabilities = JSON.parse(readFileSync(capabilitiesPath, 'utf8'))
  if (
    descriptor.name !== item.name
    || descriptor.version !== item.version
    || capabilities.plugin !== item.name
    || capabilities.plugin_version !== item.version
  ) {
    throw new Error(`Harness 插件版本不一致：${item.name}`)
  }
  if (sha256File(capabilitiesPath) !== item.capabilities_sha256) {
    throw new Error(`Harness 插件哈希不一致：${item.name}/capabilities.json`)
  }
  for (const [relativePath, expected] of Object.entries(item.sources_sha256 ?? {})) {
    if (!safeRelativePath(relativePath) || typeof expected !== 'string') {
      throw new Error(`Harness 插件冻结来源无效：${item.name}`)
    }
    const source = join(pluginRoot, relativePath)
    assertInside(pluginRoot, source)
    if (!existsSync(source) || sha256File(source) !== expected) {
      throw new Error(`Harness 插件哈希不一致：${item.name}/${relativePath}`)
    }
  }
  return pluginRoot
}

function collectFiles(root) {
  const files = []
  const walk = (directory) => {
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
      const absolute = join(directory, entry.name)
      const relativePath = relative(root, absolute).split(sep).join('/')
      if (EXCLUDED.some((pattern) => pattern.test(relativePath))) continue
      if (entry.isSymbolicLink()) throw new Error(`Harness 插件不能包含符号链接：${relativePath}`)
      if (entry.isDirectory()) walk(absolute)
      else if (entry.isFile()) files.push({ absolute, relativePath })
    }
  }
  walk(root)
  return files.sort((left, right) => left.relativePath.localeCompare(right.relativePath))
}

/**
 * Apply exact, audited desktop-layout overlays to the frozen plugin bundle.
 * The inventory digest must match before patching and is refreshed only for
 * the exact source file changed here, so unreviewed provider drift still fails.
 */
export function applyHarnessPluginCompatibilityPatches(bundleRoot, inventoryPath) {
  const root = resolve(bundleRoot)
  const resolvedInventoryPath = resolve(inventoryPath)
  const payload = JSON.parse(readFileSync(resolvedInventoryPath, 'utf8'))
  const plugin = payload?.plugins?.find((item) => item?.name === 'his-harness-core')
  if (!plugin || typeof plugin.sources_sha256 !== 'object') {
    throw new Error('Harness 插件冻结清单缺少 his-harness-core 来源')
  }
  const relativePath = 'scripts/requirement_governance.py'
  const providerPath = join(root, 'his-harness-core', relativePath)
  if (!existsSync(providerPath)) return []
  const declaredDigest = plugin.sources_sha256[relativePath]
  if (declaredDigest !== sha256File(providerPath)) {
    throw new Error('Harness 插件兼容补丁前来源哈希不一致')
  }

  const provider = readFileSync(providerPath, 'utf8')
  let patched = provider
  if (
    provider.includes(HIS_HARNESS_CORE_ROOT_CURRENT)
    && provider.includes(HIS_HARNESS_CORE_CANDIDATES_CURRENT)
  ) {
    // Already patched.
  } else {
    if (
      !provider.includes(HIS_HARNESS_CORE_ROOT_OLD)
      || !provider.includes(HIS_HARNESS_CORE_CANDIDATES_OLD)
    ) {
      throw new Error('his-harness-core 可迁移根目录契约漂移，必须人工复核后才能同步')
    }
    patched = provider
      .replace(HIS_HARNESS_CORE_ROOT_OLD, HIS_HARNESS_CORE_ROOT_CURRENT)
      .replace(
        HIS_HARNESS_CORE_CANDIDATES_OLD,
        HIS_HARNESS_CORE_CANDIDATES_CURRENT,
      )
    writeFileSync(providerPath, patched)
  }
  plugin.sources_sha256[relativePath] = sha256File(providerPath)
  writeFileSync(resolvedInventoryPath, `${JSON.stringify(payload, null, 2)}\n`)
  return [RELOCATABLE_HIS_HARNESS_CORE_ROOT_PATCH]
}

export function verifyHarnessPluginBundle(bundleRoot, inventoryPath) {
  const root = resolve(bundleRoot)
  const plugins = inventory(inventoryPath)
  if (!existsSync(root)) throw new Error(`Harness 插件目录不存在：${root}`)
  for (const item of plugins) verifyPlugin(root, item)
  const files = collectFiles(root).filter((file) => file.relativePath !== HARNESS_PLUGIN_VENDOR_MANIFEST)
  return {
    pluginCount: plugins.length,
    fileCount: files.length,
    totalBytes: files.reduce((sum, file) => sum + statSync(file.absolute).size, 0),
    manifestSha256: sha256(files.map((file) => `${file.relativePath}:${sha256File(file.absolute)}`).join('\n')),
  }
}

/**
 * Verify the checked-in release bundle against its independent whole-bundle
 * manifest. The Core inventory may intentionally describe the unpatched
 * upstream sources, so it cannot be the release-bundle verification source.
 */
export function verifyCheckedInHarnessPluginBundle(bundleRoot) {
  const root = resolve(bundleRoot)
  const manifestPath = join(root, HARNESS_PLUGIN_VENDOR_MANIFEST)
  if (!existsSync(manifestPath)) throw new Error('缺少 Harness 插件 vendor 清单')
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
  if (manifest?.schema !== 'harness-plugin-vendor.v1') {
    throw new Error('Harness 插件 vendor 清单无效')
  }
  const temporaryRoot = mkdtempSync(join(tmpdir(), 'dsh-plugin-verify-'))
  const inventoryPath = join(temporaryRoot, 'plugin_inventory.json')
  try {
    writeFrozenPluginInventoryFromBundle(root, inventoryPath)
    const summary = verifyHarnessPluginBundle(root, inventoryPath)
    for (const key of ['pluginCount', 'fileCount', 'totalBytes', 'manifestSha256']) {
      if (summary[key] !== manifest[key]) {
        throw new Error(`Harness 插件 vendor 清单漂移：${key}`)
      }
    }
    return summary
  } finally {
    rmSync(temporaryRoot, { recursive: true, force: true })
  }
}

/**
 * Bind a copied Core to the exact checked-in desktop plugin bundle.
 *
 * Upstream Core may advance its own plugin inventory before the desktop has
 * reviewed those providers. The desktop release must instead use the frozen
 * bundle recorded by VENDOR_MANIFEST.json. Every shipped plugin file is
 * included in sources_sha256; the vendor manifest remains the independent
 * whole-bundle drift check used immediately afterwards by the assembler.
 */
export function writeFrozenPluginInventoryFromBundle(bundleRoot, inventoryPath) {
  const root = resolve(bundleRoot)
  const target = resolve(inventoryPath)
  const discoveredPluginNames = readdirSync(root, { withFileTypes: true })
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name)
    .sort()
  let pluginNames = discoveredPluginNames
  if (existsSync(target)) {
    const copiedInventory = JSON.parse(readFileSync(target, 'utf8'))
    if (
      copiedInventory?.schema_version !== 'his-plugin-inventory.v1'
      || !Array.isArray(copiedInventory.plugins)
    ) {
      throw new Error('复制的 Core 插件冻结清单无效')
    }
    const preferredNames = copiedInventory.plugins.map((item) => item?.name)
    if (
      preferredNames.some((name) => typeof name !== 'string')
      || new Set(preferredNames).size !== preferredNames.length
    ) {
      throw new Error('复制的 Core 插件顺序无效')
    }
    const discovered = new Set(discoveredPluginNames)
    const preferred = preferredNames.filter((name) => discovered.has(name))
    const preferredSet = new Set(preferred)
    pluginNames = [
      ...preferred,
      ...discoveredPluginNames.filter((name) => !preferredSet.has(name)),
    ]
  }
  if (pluginNames.length === 0) throw new Error('Harness 冻结插件目录为空')
  const plugins = pluginNames.map((name) => {
    const pluginRoot = join(root, name)
    const descriptorPath = join(pluginRoot, '.codex-plugin', 'plugin.json')
    const capabilitiesPath = join(pluginRoot, 'capabilities.json')
    if (!existsSync(descriptorPath) || !existsSync(capabilitiesPath)) {
      throw new Error(`Harness 插件不完整：${name}`)
    }
    const descriptor = JSON.parse(readFileSync(descriptorPath, 'utf8'))
    const capabilities = JSON.parse(readFileSync(capabilitiesPath, 'utf8'))
    if (
      descriptor.name !== name
      || capabilities.plugin !== name
      || descriptor.version !== capabilities.plugin_version
      || !Array.isArray(capabilities.capabilities)
      || capabilities.capabilities.some(
        (item) => typeof item?.name !== 'string' || item.name.trim() === '',
      )
    ) {
      throw new Error(`Harness 插件元数据不一致：${name}`)
    }
    const files = collectFiles(pluginRoot)
    return {
      name,
      version: descriptor.version,
      capabilities_sha256: sha256File(capabilitiesPath),
      capabilities: capabilities.capabilities.map((item) => item.name),
      sources_sha256: Object.fromEntries(
        files.map((file) => [file.relativePath, sha256File(file.absolute)]),
      ),
    }
  })
  const payload = { schema_version: 'his-plugin-inventory.v1', plugins }
  mkdirSync(dirname(target), { recursive: true })
  writeFileSync(target, `${JSON.stringify(payload, null, 2)}\n`)
  return payload
}

export function copyHarnessPluginBundle({ sources, target, inventoryPath }) {
  const targetRoot = resolve(target)
  const plugins = inventory(inventoryPath)
  rmSync(targetRoot, { recursive: true, force: true })
  mkdirSync(targetRoot, { recursive: true })
  for (const item of plugins) {
    const source = sources[item.name]
    if (typeof source !== 'string') throw new Error(`缺少 Harness 插件来源：${item.name}`)
    const sourceRoot = resolve(source)
    const sourceParent = dirname(sourceRoot)
    verifyPlugin(sourceParent, item)
    for (const file of collectFiles(sourceRoot)) {
      const destination = join(targetRoot, item.name, file.relativePath)
      mkdirSync(dirname(destination), { recursive: true })
      cpSync(file.absolute, destination)
    }
  }
  return verifyHarnessPluginBundle(targetRoot, inventoryPath)
}

export function copyCheckedInHarnessPluginBundle(source, target, inventoryPath) {
  const plugins = inventory(inventoryPath)
  return copyHarnessPluginBundle({
    sources: Object.fromEntries(plugins.map((item) => [item.name, join(resolve(source), item.name)])),
    target,
    inventoryPath,
  })
}

export function writeHarnessPluginVendorManifest(target, summary, sources = {}) {
  const payload = {
    schema: 'harness-plugin-vendor.v1',
    syncedAt: new Date().toISOString(),
    sources,
    ...summary,
  }
  writeFileSync(join(resolve(target), HARNESS_PLUGIN_VENDOR_MANIFEST), `${JSON.stringify(payload, null, 2)}\n`)
  return payload
}

export function syncHarnessPluginVendor({ sourceRoot, target, inventoryPath }) {
  const resolvedSourceRoot = resolve(sourceRoot)
  const resolvedTarget = resolve(target)
  if (resolvedSourceRoot === resolvedTarget) {
    throw new Error('Harness 插件来源与 vendor 目标不能相同')
  }
  const pluginNames = inventory(inventoryPath).map((item) => item.name)
  const sources = Object.fromEntries(
    pluginNames.map((name) => [name, join(resolvedSourceRoot, name)]),
  )
  const temporaryRoot = mkdtempSync(join(tmpdir(), 'dsh-plugin-inventory-'))
  const stagedInventory = join(temporaryRoot, 'plugin_inventory.json')
  try {
    writeFileSync(stagedInventory, readFileSync(resolve(inventoryPath)))
    copyHarnessPluginBundle({ sources, target: resolvedTarget, inventoryPath: stagedInventory })
    const compatibilityPatches = pluginNames.includes('his-harness-core')
      ? applyHarnessPluginCompatibilityPatches(resolvedTarget, stagedInventory)
      : []
    const summary = verifyHarnessPluginBundle(resolvedTarget, stagedInventory)
    const manifest = writeHarnessPluginVendorManifest(resolvedTarget, summary, sources)
    return { ...summary, compatibilityPatches, manifest }
  } finally {
    rmSync(temporaryRoot, { recursive: true, force: true })
  }
}

export function writePackagedCapabilitiesConfig(coreRoot, pluginNames) {
  const path = join(resolve(coreRoot), 'config', 'capabilities.json')
  const payload = JSON.parse(readFileSync(path, 'utf8'))
  // 配置位于 harness-core/config，冻结插件位于 harness-core 的兄弟目录
  // plugins；因此必须从 config 先退两级，安装到任意目录后仍可解析。
  payload.plugin_roots = pluginNames.map((name) => `../../plugins/${name}`)
  writeFileSync(path, `${JSON.stringify(payload, null, 2)}\n`)
  return payload
}
