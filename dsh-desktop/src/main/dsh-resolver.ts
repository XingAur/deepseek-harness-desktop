import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'

export interface DshResolution { binPath: string; version: string; source: 'user' | 'bundled' }

const binIn = (root: string) => join(root, 'node_modules', '@deepseek-ai', 'dsh', 'lib', 'bin.js')
const numeric = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true }) > 0

export function resolveDsh(runtimeDshDir: string, bundledDir: string): DshResolution {
  const valid = existsSync(runtimeDshDir)
    ? readdirSync(runtimeDshDir).filter(v => existsSync(binIn(join(runtimeDshDir, v))))
    : []
  if (valid.length > 0) {
    const latest = valid.reduce((m, v) => (numeric(v, m) ? v : m))
    return { binPath: binIn(join(runtimeDshDir, latest)), version: latest, source: 'user' }
  }
  const pkg = JSON.parse(readFileSync(join(bundledDir, 'node_modules', '@deepseek-ai', 'dsh', 'package.json'), 'utf8'))
  return { binPath: binIn(bundledDir), version: pkg.version, source: 'bundled' }
}
