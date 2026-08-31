import { mkdirSync, mkdtempSync, readlinkSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { restoreRuntimeDependencyCache } from './runtime-dependency-cache-copy.mjs'

const roots: string[] = []
afterEach(() => { for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true }) })

describe('restoreRuntimeDependencyCache', () => {
  it('requests a recursive copy that preserves relative npm links', () => {
    const cpSync = vi.fn()
    const readFileSync = vi.fn((path: string) => JSON.stringify({ version: path.includes('@deepseek-ai') ? '0.1.1-rc.2' : '11.7.0' }))

    expect(restoreRuntimeDependencyCache('/runtime/app', '/cache/node_modules', {
      dshVersion: '0.1.1-rc.2', pnpmVersion: '11.7.0',
    }, { existsSync: () => true, readFileSync, cpSync })).toBe(true)
    expect(cpSync).toHaveBeenCalledWith(
      resolve('/cache/node_modules'),
      resolve('/runtime/app/node_modules'),
      { recursive: true, verbatimSymlinks: true },
    )
  })

  it.skipIf(process.platform === 'win32')('keeps an npm-style relative bin link relative after a real copy', () => {
    const root = mkdtempSync(join(tmpdir(), 'dsh-runtime-cache-copy-'))
    roots.push(root)
    const cache = join(root, 'cache')
    const app = join(root, 'app')
    writePackage(cache, '@deepseek-ai/dsh', '0.1.1-rc.2')
    writePackage(cache, 'pnpm', '11.7.0')
    const tool = join(cache, 'tool')
    mkdirSync(join(cache, '.bin'), { recursive: true })
    mkdirSync(tool, { recursive: true })
    writeFileSync(join(tool, 'bin.js'), '')
    symlinkSync('../tool/bin.js', join(cache, '.bin', 'tool'))

    restoreRuntimeDependencyCache(app, cache, { dshVersion: '0.1.1-rc.2', pnpmVersion: '11.7.0' })

    expect(readlinkSync(join(app, 'node_modules', '.bin', 'tool'))).toBe('../tool/bin.js')
  })

  it('rejects an incomplete or wrong-version cache before copying', () => {
    const cpSync = vi.fn()
    expect(() => restoreRuntimeDependencyCache('/runtime/app', '/cache', {
      dshVersion: '0.1.1-rc.2', pnpmVersion: '11.7.0',
    }, { existsSync: () => false, readFileSync: vi.fn(), cpSync })).toThrow(/incomplete/)
    expect(cpSync).not.toHaveBeenCalled()
  })
})

function writePackage(cache: string, name: string, version: string) {
  const directory = join(cache, ...name.split('/'))
  mkdirSync(directory, { recursive: true })
  writeFileSync(join(directory, 'package.json'), JSON.stringify({ name, version }))
}
