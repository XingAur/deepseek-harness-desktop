import { existsSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

export function findCompatibleRuntimeDependencyCache(options) {
  for (const candidate of options.candidates) {
    const cache = resolve(candidate)
    const dshVersion = packageVersion(join(cache, '@deepseek-ai', 'dsh', 'package.json'))
    const pnpmVersion = packageVersion(join(cache, 'pnpm', 'package.json'))
    if (dshVersion === options.dshVersion && pnpmVersion === options.pnpmVersion) return cache
  }
  return undefined
}

function packageVersion(path) {
  if (!existsSync(path)) return undefined
  try {
    const value = JSON.parse(readFileSync(path, 'utf8'))
    return typeof value.version === 'string' ? value.version : undefined
  } catch {
    return undefined
  }
}
