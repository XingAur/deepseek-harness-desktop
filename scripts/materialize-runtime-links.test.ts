import {
  chmodSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { describe, expect, it } from 'vitest'

import { materializeRuntimeLinks } from './materialize-runtime-links.mjs'

function supportsSymbolicLinks() {
  const root = mkdtempSync(join(tmpdir(), 'dsh-runtime-links-symlink-probe-'))
  try {
    const target = join(root, 'target')
    writeFileSync(target, '')
    symlinkSync(target, join(root, 'link'))
    return true
  } catch {
    return false
  } finally {
    rmSync(root, { recursive: true, force: true })
  }
}

const symlinkIt = supportsSymbolicLinks() ? it : it.skip

describe('Runtime archive link materialization', () => {
  symlinkIt('replaces relative and build-root links with regular files', () => {
    const root = mkdtempSync(join(tmpdir(), 'dsh-runtime-links-'))
    try {
      const stage = join(root, 'stage')
      const downloads = join(root, 'downloads')
      const bin = join(stage, 'bin')
      mkdirSync(bin, { recursive: true })
      mkdirSync(downloads, { recursive: true })
      const node = join(downloads, 'node')
      writeFileSync(node, 'node binary')
      chmodSync(node, 0o755)
      symlinkSync(node, join(bin, 'node'))
      writeFileSync(join(stage, 'target.js'), 'target')
      symlinkSync('../target.js', join(bin, 'target.js'))

      materializeRuntimeLinks(stage, root)

      expect(lstatSync(join(bin, 'node')).isSymbolicLink()).toBe(false)
      expect(readFileSync(join(bin, 'node'), 'utf8')).toBe('node binary')
      if (process.platform !== 'win32') {
        expect(lstatSync(join(bin, 'node')).mode & 0o111).not.toBe(0)
      }
      expect(lstatSync(join(bin, 'target.js')).isSymbolicLink()).toBe(false)
      expect(readFileSync(join(bin, 'target.js'), 'utf8')).toBe('target')
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  symlinkIt('rejects links that resolve outside the build root', () => {
    const root = mkdtempSync(join(tmpdir(), 'dsh-runtime-links-'))
    const outside = mkdtempSync(join(tmpdir(), 'dsh-runtime-outside-'))
    try {
      const stage = join(root, 'stage')
      mkdirSync(stage, { recursive: true })
      writeFileSync(join(outside, 'secret'), 'outside')
      symlinkSync(join(outside, 'secret'), join(stage, 'secret'))

      expect(() => materializeRuntimeLinks(stage, root)).toThrow(/构建目录之外/)
    } finally {
      rmSync(root, { recursive: true, force: true })
      rmSync(outside, { recursive: true, force: true })
    }
  })
})
