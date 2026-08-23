import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'

export const REQUIRED_DSH_PEER_PACKAGES = Object.freeze([
  '@deepseek-ai/dsh-anonymous-user-id',
  '@deepseek-ai/dsh-atomic-write',
  '@deepseek-ai/dsh-authorization',
  '@deepseek-ai/dsh-bash-local',
  '@deepseek-ai/dsh-code-runtime',
  '@deepseek-ai/dsh-compaction',
  '@deepseek-ai/dsh-fs',
  '@deepseek-ai/dsh-output-retention',
  '@deepseek-ai/dsh-sandbox',
  '@deepseek-ai/dsh-scope',
  '@deepseek-ai/dsh-session-telemetry',
  '@deepseek-ai/dsh-session-title-llm',
  '@deepseek-ai/dsh-shell',
  '@deepseek-ai/dsh-spill',
  '@deepseek-ai/dsh-subagent-in-process-driver',
  '@deepseek-ai/dsh-timeout',
  '@deepseek-ai/dsh-workflow',
])

export function runtimePeerDependencies(dshVersion) {
  if (!/^0\.1\.\d+-rc\.\d+$/.test(dshVersion)) {
    throw new Error(`Unsupported DeepSeek Harness Runtime version: ${dshVersion}`)
  }

  return {
    '@deepseek-ai/cordis-plugin-group': '1.0.1',
    '@deepseek-ai/dsh-invariants': dshVersion,
    react: '18.3.1',
    'react-dom': '18.3.1',
    ...Object.fromEntries(REQUIRED_DSH_PEER_PACKAGES.map((name) => [name, dshVersion])),
  }
}

export function findMissingRuntimePeers(appDir) {
  const appRoot = resolve(appDir)
  const nodeModules = join(appRoot, 'node_modules')
  if (!existsSync(nodeModules)) throw new Error(`Runtime node_modules does not exist: ${nodeModules}`)

  const missing = new Map()
  for (const manifestPath of packageManifests(nodeModules)) {
    const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
    for (const peerName of Object.keys(manifest.peerDependencies ?? {})) {
      if (manifest.peerDependenciesMeta?.[peerName]?.optional === true) continue
      if (canResolvePackage(dirname(manifestPath), appRoot, peerName)) continue

      const consumer = `${manifest.name ?? '(anonymous)'}@${manifest.version ?? 'unknown'}`
      const requiredBy = missing.get(peerName) ?? new Set()
      requiredBy.add(consumer)
      missing.set(peerName, requiredBy)
    }
  }

  return [...missing.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([name, requiredBy]) => ({ name, requiredBy: [...requiredBy].sort() }))
}

export function assertRuntimePeerDependencies(appDir) {
  const missing = findMissingRuntimePeers(appDir)
  if (missing.length === 0) return

  const details = missing
    .map(({ name, requiredBy }) => `${name} required by ${requiredBy.join(', ')}`)
    .join('\n')
  throw new Error(`Runtime is missing required peer dependencies:\n${details}`)
}

function packageManifests(nodeModules) {
  const manifests = []
  for (const entry of readdirSync(nodeModules, { withFileTypes: true })) {
    if (!entry.isDirectory()) continue
    const entryPath = join(nodeModules, entry.name)
    if (entry.name.startsWith('@')) {
      for (const scopedEntry of readdirSync(entryPath, { withFileTypes: true })) {
        if (scopedEntry.isDirectory()) collectPackage(join(entryPath, scopedEntry.name), manifests)
      }
    } else {
      collectPackage(entryPath, manifests)
    }
  }
  return manifests
}

function collectPackage(packageDir, manifests) {
  const manifestPath = join(packageDir, 'package.json')
  if (!existsSync(manifestPath)) return
  manifests.push(manifestPath)

  const nestedNodeModules = join(packageDir, 'node_modules')
  if (existsSync(nestedNodeModules)) manifests.push(...packageManifests(nestedNodeModules))
}

function canResolvePackage(startDir, appRoot, packageName) {
  let current = startDir
  while (true) {
    if (existsSync(join(current, 'node_modules', ...packageName.split('/'), 'package.json'))) return true
    if (current === appRoot) return false
    const parent = dirname(current)
    if (parent === current || !parent.startsWith(appRoot)) return false
    current = parent
  }
}
