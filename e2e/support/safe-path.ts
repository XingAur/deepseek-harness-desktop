import { existsSync, lstatSync, mkdirSync, realpathSync } from 'node:fs'
import { dirname, resolve } from 'node:path'

function normalize(path: string): string { return resolve(path).replace(/^\\\\\?\\/, '').toLowerCase() }
export function assertSafePath(path: string): string {
  const target = resolve(path)
  let current = target
  while (true) {
    try {
      const stat = lstatSync(current)
      if (stat.isSymbolicLink() || normalize(realpathSync.native(current)) !== normalize(current)) throw new Error(`不安全路径：${path}`)
      const parent = dirname(current)
      if (parent === current) break
      current = parent
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
      const parent = dirname(current)
      if (parent === current) break
      current = parent
    }
  }
  return target
}
export function prepareSafeDirectory(path: string): string { const target = assertSafePath(path); mkdirSync(target, { recursive: true }); return assertSafePath(target) }
export function assertSafeLeaf(path: string): string {
  const target = assertSafePath(path)
  try { const stat = lstatSync(target); if (stat.isSymbolicLink() || normalize(realpathSync.native(target)) !== normalize(target)) throw new Error(`不安全文件：${path}`) } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error }
  return target
}
