import { closeSync, existsSync, fstatSync, lstatSync, mkdirSync, openSync, readFileSync, realpathSync, writeFileSync } from 'node:fs'
import { dirname, isAbsolute, join, relative, resolve } from 'node:path'

const ROOT_MARKER = '.dsh-e2e-root-owned'
const ROOT_MARKER_VALUE = 'E2E-owned'
const ARTIFACTS_MARKER = '.dsh-e2e-artifacts-owned'
const ARTIFACTS_MARKER_VALUE = 'E2E-owned'

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

// Callers that remove a child below a validated E2E root use this to reject a
// symlink or junction replacement immediately before touching that child.
export function assertSafeExistingE2EPath(path) {
  assertSafeExistingPath(path)
}

// Initializes both paths with the same fail-closed ownership contract used by
// the later setup and runner validation.  This lets CI create its per-run
// artifacts directory before fixture tests without adopting any existing data.
export function initializeOwnedE2EPaths(rootPath, artifactsPath) {
  const e2eRoot = initializeOwnedE2ERoot(rootPath)
  const artifactsRoot = resolve(artifactsPath)
  assertArtifactsRootIsControlled(e2eRoot, artifactsRoot)

  if (existsSync(artifactsRoot)) {
    validateOwnedMarker(artifactsRoot, ARTIFACTS_MARKER, ARTIFACTS_MARKER_VALUE, 'E2E artifacts root 未受本套件所有权标记保护')
    return { e2eRoot, artifactsRoot }
  }

  const parent = dirname(artifactsRoot)
  assertSafeExistingPath(parent)
  if (!lstatSync(parent).isDirectory()) throw new Error('E2E artifacts root 父目录无效')
  try {
    mkdirSync(artifactsRoot)
  } catch (error) {
    if (error?.code !== 'EEXIST') throw error
    validateOwnedMarker(artifactsRoot, ARTIFACTS_MARKER, ARTIFACTS_MARKER_VALUE, 'E2E artifacts root 未受本套件所有权标记保护')
    return { e2eRoot, artifactsRoot }
  }
  try {
    assertSafeExistingPath(artifactsRoot)
    writeFileSync(join(artifactsRoot, ARTIFACTS_MARKER), ARTIFACTS_MARKER_VALUE, { encoding: 'utf8', flag: 'wx' })
    validateOwnedMarker(artifactsRoot, ARTIFACTS_MARKER, ARTIFACTS_MARKER_VALUE, 'E2E artifacts root 未受本套件所有权标记保护')
    return { e2eRoot, artifactsRoot }
  } catch (error) {
    // Do not remove a path whose marker publication failed: it may have been
    // replaced or populated concurrently, so preserving it is fail-closed.
    throw error
  }
}

export function validateOwnedE2EPaths(paths) {
  validateOwnedE2ERoot(paths.e2eRoot)
  const artifactsRoot = resolve(paths.artifactsRoot)
  assertArtifactsRootIsControlled(resolve(paths.e2eRoot), artifactsRoot)
  validateOwnedMarker(artifactsRoot, ARTIFACTS_MARKER, ARTIFACTS_MARKER_VALUE, 'E2E artifacts root 未受本套件所有权标记保护')
}

function assertArtifactsRootIsControlled(e2eRoot, artifactsRoot) {
  const relation = relative(e2eRoot, artifactsRoot)
  if (relation === '' || relation === '..' || relation.startsWith('..\\') || relation.startsWith('../') || isAbsolute(relation)) {
    throw new Error('E2E artifacts root 不在受控 E2E root 内')
  }
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
    if (safePathKey(realpathSync.native(marker)) !== safePathKey(realpathSync.native(join(root, markerName)))) throw new Error('marker-reparse')
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
  // 与 e2e/support/safe-path.ts 相同的语义：以符号链接检测为准，豁免
  // 文件系统根一级的系统布局符号链接（macOS /var）；非符号链接节点的
  // 规范拼写差异（Windows 8.3 短名等）视为同一物理目录。
  let current = resolve(path)
  while (true) {
    const stat = lstatSync(current)
    if (stat.isSymbolicLink() && !isSystemLayoutAlias(current)) {
      throw new Error(`不安全路径：${path}`)
    }
    const parent = dirname(current)
    if (parent === current) return
    current = parent
  }
}

function isSystemLayoutAlias(path) {
  const parent = dirname(path)
  if (dirname(parent) !== parent) return false
  try {
    return lstatSync(path).isSymbolicLink() && safePathKey(realpathSync.native(path)) !== safePathKey(path)
  } catch {
    return false
  }
}

function safePathKey(path) {
  const normalized = resolve(path).replace(/^\\\\\?\\/, '')
  return process.platform === 'win32' ? normalized.toLowerCase() : normalized
}

function sameFile(left, right) {
  return left.dev === right.dev && left.ino === right.ino && left.size === right.size && left.mtimeMs === right.mtimeMs
}
