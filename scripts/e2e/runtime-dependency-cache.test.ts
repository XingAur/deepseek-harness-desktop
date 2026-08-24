import { mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { findCompatibleRuntimeDependencyCache } from './runtime-dependency-cache.mjs'

const roots: string[] = []

afterEach(() => {
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true })
})

describe('Runtime dependency cache selection', () => {
  it('skips stale caches and selects a cache matching both managed versions', () => {
    const stale = dependencyCache('0.1.0-rc.8', '11.7.0')
    const current = dependencyCache('0.1.1-rc.2', '11.7.0')

    expect(findCompatibleRuntimeDependencyCache({
      candidates: [stale, current],
      dshVersion: '0.1.1-rc.2',
      pnpmVersion: '11.7.0',
    })).toBe(current)
  })

  it('returns undefined when every available cache is stale or incomplete', () => {
    const stale = dependencyCache('0.1.0-rc.8', '11.7.0')
    const incomplete = join(createRoot(), 'node_modules')
    mkdirSync(incomplete, { recursive: true })

    expect(findCompatibleRuntimeDependencyCache({
      candidates: [stale, incomplete, join(createRoot(), 'missing')],
      dshVersion: '0.1.1-rc.2',
      pnpmVersion: '11.7.0',
    })).toBeUndefined()
  })
})

function dependencyCache(dshVersion: string, pnpmVersion: string): string {
  const cache = join(createRoot(), 'node_modules')
  writePackage(join(cache, '@deepseek-ai', 'dsh'), '@deepseek-ai/dsh', dshVersion)
  writePackage(join(cache, 'pnpm'), 'pnpm', pnpmVersion)
  return cache
}

function writePackage(directory: string, name: string, version: string): void {
  mkdirSync(directory, { recursive: true })
  writeFileSync(join(directory, 'package.json'), JSON.stringify({ name, version }), 'utf8')
}

function createRoot(): string {
  const root = join(tmpdir(), `dsh-runtime-cache-${crypto.randomUUID()}`)
  roots.push(root)
  return root
}
