import { closeSync, existsSync, fstatSync, lstatSync, mkdirSync, openSync, readFileSync, realpathSync, writeFileSync } from 'node:fs'
import { dirname, isAbsolute, join, relative, resolve } from 'node:path'

const ROOT_MARKER = '.dsh-e2e-root-owned'
const ROOT_MARKER_VALUE = 'E2E-owned'

// Both setup and the runner use this entry point.  In particular, an explicit
// root is not a trust boundary: it receives the same marker checks as the
// default root and is only initialized when it did not already exist.
export function initializeOwnedE2ERoot(path) {
  const root = resolve(path)
  if (existsSync(root)) {
    validateOwnedE2ERoot(root)
    return root
  }

  const parent = dirname(root)
  assertSafeExistingPath(parent)
  try {
    mkdirSync(root)
  } catch (error) {
    if (error?.code !== 'EEXIST') throw error
    validateOwnedE2ERoot(root)
    return root
  }
  try {
    assertSafeExistingPath(root)
    writeFileSync(join(root, ROOT_MARKER), ROOT_MARKER_VALUE, { encoding: 'utf8', flag: 'wx' })
    validateOwnedE2ERoot(root)
    return root
  } catch (error) {
    // Do not remove a root after a failed publication: another process might
    // have populated it, and failing closed preserves that evidence.
    throw error
  }
}

// Kept as a compatibility alias for callers/tests introduced while only the
// default path needed initialization.  New callers should use the ownership
// based name above so explicit and default roots cannot diverge.
export const initializeDefaultE2ERoot = initializeOwnedE2ERoot

export function validateOwnedE2EPaths(paths) {
  validateOwnedE2ERoot(paths.e2eRoot)
  const artifactsRoot = resolve(paths.artifactsRoot)
  const relation = relative(resolve(paths.e2eRoot), artifactsRoot)
  if (relation === '' || relation === '..' || relation.startsWith('..\\') || relation.startsWith('../') || isAbsolute(relation)) {
    throw new Error('E2E artifacts root 不在受控 E2E root 内')
  }
  validateOwnedMarker(artifactsRoot, '.dsh-e2e-artifacts-owned', 'E2E-owned', 'E2E artifacts root 未受本套件所有权标记保护')
}

function validateOwnedE2ERoot(root) {
  validateOwnedMarker(root, ROOT_MARKER, ROOT_MARKER_VALUE, '默认 E2E root 未受本套件所有权标记保护')
}

function validateOwnedMarker(root, markerName, markerValue, failureMessage) {
  let descriptor
  try {
    assertSafeExistingPath(root)
    if (!lstatSync(root).isDirectory()) throw new Error('not-directory')
    const marker = join(root, markerName)
    assertSafeExistingPath(marker)
    const metadata = lstatSync(marker)
    if (!metadata.isFile() || metadata.isSymbolicLink()) throw new Error('marker-not-ordinary-file')
    if (safePathKey(realpathSync.native(marker)) !== safePathKey(marker)) throw new Error('marker-reparse')
    descriptor = openSync(marker, 'r')
    const opened = fstatSync(descriptor)
    if (!opened.isFile() || !sameFile(metadata, opened)) throw new Error('marker-replaced')
    const contents = readFileSync(descriptor, 'utf8')
    const afterRead = fstatSync(descriptor)
    const afterPath = lstatSync(marker)
    assertSafeExistingPath(marker)
    if (!sameFile(opened, afterRead) || !sameFile(afterRead, afterPath) || contents !== markerValue) {
      throw new Error('marker-invalid')
    }
  } catch {
    throw new Error(failureMessage)
  } finally {
    if (descriptor !== undefined) closeSync(descriptor)
  }
}

function assertSafeExistingPath(path) {
  let current = resolve(path)
  while (true) {
    const stat = lstatSync(current)
    if (stat.isSymbolicLink() || safePathKey(realpathSync.native(current)) !== safePathKey(current)) {
      throw new Error(`不安全路径：${path}`)
    }
    const parent = dirname(current)
    if (parent === current) return
    current = parent
  }
}

function safePathKey(path) {
  const normalized = resolve(path).replace(/^\\\\\?\\/, '')
  return process.platform === 'win32' ? normalized.toLowerCase() : normalized
}

function sameFile(left, right) {
  return left.dev === right.dev && left.ino === right.ino && left.size === right.size && left.mtimeMs === right.mtimeMs
}
