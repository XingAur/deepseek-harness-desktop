import { lstatSync, mkdirSync, readdirSync, realpathSync } from 'node:fs'
import { dirname, resolve } from 'node:path'

function normalize(path: string): string { return resolve(path).replace(/^\\\\\?\\/, '').toLowerCase() }

/**
 * Validate that the checked path contains no attacker-controlled symlink
 * redirection while tolerating operating-system layout aliases (macOS
 * `os.tmpdir()` starts with `/var`, a system symlink to `/private/var`).
 *
 * Depth-1 symlinks at the filesystem root can only be system layout — an
 * attacker plants symlinks deeper inside writable trees. A realpath mismatch
 * on a deeper non-symlink node is accepted only when it is fully explained by
 * a depth-1 alias prefix; every other mismatch is a redirection and rejected.
 */
export function assertSafePath(path: string): string {
  const target = resolve(path)
  const aliases = systemAliasMounts()
  let current = target
  while (true) {
    const stat = tryLstat(current)
    if (stat !== undefined) {
      const real = realpathSync.native(current)
      if (stat.isSymbolicLink()) {
        if (!isDepthOneAlias(current, aliases)) throw new Error(`不安全路径：${path}`)
      } else if (normalize(real) !== normalize(current) && !explainedByAlias(current, real, aliases)) {
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

function explainedByAlias(path: string, real: string, aliases: AliasMount[]): boolean {
  for (const mount of aliases) {
    if (normalize(path).startsWith(`${normalize(mount.alias)}/`)) {
      const expected = `${mount.real}${path.slice(mount.alias.length)}`
      if (normalize(real) === normalize(expected)) return true
    }
  }
  return false
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

export function prepareSafeDirectory(path: string): string { const target = assertSafePath(path); mkdirSync(target, { recursive: true }); return assertSafePath(target) }
export function assertSafeLeaf(path: string): string {
  const target = assertSafePath(path)
  try { const stat = lstatSync(target); if (stat.isSymbolicLink() || normalize(realpathSync.native(target)) !== normalize(target)) throw new Error(`不安全文件：${path}`) } catch (error) { if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error }
  return target
}
