import { lstatSync, mkdirSync, readdirSync, realpathSync } from 'node:fs'
import { dirname, resolve } from 'node:path'

function normalize(path: string): string { return resolve(path).replace(/^\\\\\?\\/, '').toLowerCase() }

/**
 * Validate that the checked path contains no attacker-controlled symlink
 * redirection while tolerating operating-system path aliases:
 *
 * - macOS `os.tmpdir()` starts with `/var`, a system symlink to `/private/var`;
 * - Windows temporary paths frequently use 8.3 short names (`RUNNER~1`) whose
 *   canonical form differs textually from the given spelling.
 *
 * Detection is anchored on `lstat`: a redirection always goes through a
 * symlink at some node of the path, and only depth-1 symlinks (filesystem
 * layout such as `/var`) are excused. Non-symlink nodes never fail on
 * textual canonical mismatches — those are alias spellings of the same
 * physical directory, which is exactly what `realpath` resolves for callers.
 */
export function assertSafePath(path: string): string {
  const target = resolve(path)
  const aliases = systemAliasMounts()
  let current = target
  while (true) {
    const stat = tryLstat(current)
    if (stat !== undefined && stat.isSymbolicLink()) {
      const real = tryRealpath(current)
      if (real === undefined) throw new Error(`不安全路径：${path}`)
      if (!isDepthOneAlias(current, aliases) && normalize(real) !== normalize(current)) {
        throw new Error(`不安全路径：${path}`)
      }
    }
    const parent = dirname(current)
    if (parent === current) break
    current = parent
  }
  return target
}

type AliasMount = { alias: string; real: string }

function isDepthOneAlias(path: string, aliases: AliasMount[]): boolean {
  return aliases.some((mount) => normalize(mount.alias) === normalize(path))
}

/** Enumerate depth-1 symlink mounts of the filesystem root (system layout). */
function systemAliasMounts(): AliasMount[] {
  const mounts: AliasMount[] = []
  if (process.platform === 'win32') return mounts
  try {
    for (const entry of readdirSync('/', { withFileTypes: true })) {
      const full = `/${entry.name}`
      try {
        if (lstatSync(full).isSymbolicLink()) {
          const real = realpathSync.native(full)
          if (normalize(real) !== normalize(full)) mounts.push({ alias: full, real })
        }
      } catch {
        // An unreadable root entry is not an alias we can reason about.
      }
    }
  } catch {
    // Root enumeration failed elsewhere: behave like the strict original.
  }
  return mounts
}

function tryLstat(path: string) {
  try {
    return lstatSync(path)
  } catch {
    return undefined
  }
}

function tryRealpath(path: string): string | undefined {
  try {
    return realpathSync.native(path)
  } catch {
    return undefined
  }
}

export function prepareSafeDirectory(path: string): string { const target = assertSafePath(path); mkdirSync(target, { recursive: true }); return assertSafePath(target) }
export function assertSafeLeaf(path: string): string {
  const target = assertSafePath(path)
  try { const stat = lstatSync(target); if (stat.isSymbolicLink()) throw new Error(`不安全文件：${path}`) } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error }
  return target
}
