import { cpSync, existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

const defaultFs = { cpSync, existsSync, readFileSync }

export function restoreRuntimeDependencyCache(appDirectory, cacheValue, versions, adapters = {}) {
  if (!cacheValue) return false
  const fs = { ...defaultFs, ...adapters }
  const cache = resolve(cacheValue)
  const dshPackage = join(cache, '@deepseek-ai', 'dsh', 'package.json')
  const pnpmPackage = join(cache, 'pnpm', 'package.json')
  if (!fs.existsSync(dshPackage) || !fs.existsSync(pnpmPackage)) throw new Error('--dependency-cache is incomplete')
  if (JSON.parse(fs.readFileSync(dshPackage, 'utf8')).version !== versions.dshVersion) throw new Error('--dependency-cache has the wrong DSH version')
  if (JSON.parse(fs.readFileSync(pnpmPackage, 'utf8')).version !== versions.pnpmVersion) throw new Error('--dependency-cache has the wrong pnpm version')
  fs.cpSync(cache, join(appDirectory, 'node_modules'), { recursive: true, verbatimSymlinks: true })
  return true
}
