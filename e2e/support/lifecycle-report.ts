import {
  closeSync,
  existsSync,
  fstatSync,
  lstatSync,
  mkdirSync,
  mkdtempSync,
  openSync,
  readFileSync,
  readdirSync,
  renameSync,
  rmdirSync,
  unlinkSync,
  writeFileSync,
  type Stats,
} from 'node:fs'
import { randomUUID } from 'node:crypto'
import { tmpdir } from 'node:os'
import { dirname, isAbsolute, join, parse, relative, resolve } from 'node:path'
import { assertSafeLeaf, assertSafePath } from './safe-path'

export interface RedactionRoots {
  dataRoot: string
  e2eRoot: string
  userHome: string
  temp: string
}

const FORBIDDEN_KEYS = /^(apiKey|authorization|cookie|prompt|response|messages?)$/i
const ALLOWED_KEYS = new Set([
  'schemaVersion',
  'mode',
  'stage',
  'status',
  'category',
  'durationMs',
  'desktopVersion',
  'runtimeVersion',
  'installerSha256',
  'snapshot',
  'differences',
  'path',
  'startedAt',
  'finishedAt',
  'runtime',
  'profile',
  'project',
  'sessionIds',
  'version',
  'activeDirToken',
  'selectedId',
  'lastKnownGoodId',
  'revision',
  'pending',
  'workspaceId',
  'pathToken',
  'sentinelSha256',
  'artifactRoot',
  'runtimeArchive',
  'signingState',
  'sourceCommit',
  'installers',
  'baseline',
  'candidate',
  'sha256',
  'installerPath',
  'exitCode',
  'uninstallKey',
  'installRoot',
  'appBinary',
  'shortcuts',
  'dataRoot',
  'provisioningReceipt',
  'completedInstallEntry',
  'activeCandidate',
  'generationId',
  'phase',
  'recordedAt',
])

const CLASSIFIED_JSON = [
  'lifecycle-report.json',
  'instrumented-setup.json',
  'installer-records/latest-install.json',
  'generation-timeline.json',
] as const
const CLASSIFIED_SCREENSHOT = /^(quick|full)-(baseline|candidate|preserve-all|delete-app-data|delete-all)-(failure|final)\.png$/
const ARTIFACTS_OWNERSHIP_MARKER = '.dsh-e2e-artifacts-owned'
const ARTIFACTS_OWNERSHIP_VALUE = 'E2E-owned'
const FAILURE_RECORD = { schemaVersion: 1, stage: 'upload-safe', status: 'redaction-failed' } as const
const SENSITIVE_TEXT = [
  /\bsk-[A-Za-z0-9_-]{3,}\b/i,
  /\bauthorization\b/i,
  /(?:\\\\\?\\)?[A-Za-z]:[\\/]+Users[\\/]+[^\\/\s"'<>]+/i,
  /\b(?:private|secret|sensitive|raw|test)\s+(?:prompt|response|messages?|conversation|model output)\b/i,
  /\b(?:E2E_PONG|FAKE_MODEL_REPLY|SESSION_CONTRACT_(?:PROMPT|PONG|REPLY))\b/i,
  /\bE2E\s+第[一二三四五六七八九十]+会话/i,
  /<\|(?:user|assistant)\|>/i,
]

interface JsonObject {
  [key: string]: unknown
}

interface RootReplacement {
  token: string
  sourceLength: number
  pattern: RegExp
}

export function sanitizeLifecycleReport(value: unknown, roots: RedactionRoots): unknown {
  const replacements = buildRootReplacements(roots)
  return sanitizeValue(value, replacements)
}

export function lifecycleRedactionRoots(dataRoot: string): RedactionRoots {
  return {
    dataRoot: stableRoot(dataRoot, 'data-root-invalid'),
    e2eRoot: stableRoot(process.env.DSH_E2E_ROOT, 'e2e-root-invalid'),
    userHome: stableRoot(firstNonBlank(process.env.USERPROFILE, process.env.HOME), 'user-home-invalid'),
    temp: stableRoot(tmpdir(), 'temp-root-invalid'),
  }
}

export function initializeE2EArtifactsRoot(path: string, e2eRootPath: string): string {
  const e2eRoot = verifyControlledE2ERoot(e2eRootPath)
  const artifactsRoot = stableRoot(path, 'artifacts-root-invalid')
  assertStrictDescendant(e2eRoot, artifactsRoot)
  if (existsSync(artifactsRoot)) return verifyOwnedArtifactsRoot(artifactsRoot)

  const parent = assertSafePath(dirname(artifactsRoot))
  if (!lstatSync(parent).isDirectory()) throw new Error('artifacts-parent-invalid')
  assertContainedOrEqual(e2eRoot, parent)
  verifyControlledE2ERoot(e2eRoot)
  try {
    mkdirSync(assertSafeLeaf(artifactsRoot))
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'EEXIST') return verifyOwnedArtifactsRoot(artifactsRoot)
    throw error
  }
  verifyControlledE2ERoot(e2eRoot)
  const marker = assertContainedLeaf(join(artifactsRoot, ARTIFACTS_OWNERSHIP_MARKER), artifactsRoot)
  writeFileSync(marker, ARTIFACTS_OWNERSHIP_VALUE, { encoding: 'utf8', flag: 'wx' })
  verifyControlledE2ERoot(e2eRoot)
  return verifyOwnedArtifactsRoot(artifactsRoot)
}

export function stageSafeLifecycleArtifacts(input: {
  artifactsRoot: string
  roots: RedactionRoots
}): void {
  // Node does not expose openat/renameat-style directory-handle operations here. Repeated
  // identity/reparse checks plus exclusive writes and atomic directory publication narrow
  // replacement windows and fail closed, but do not claim absolute TOCTOU elimination.
  const e2eRoot = verifyControlledE2ERoot(input.roots.e2eRoot)
  const requestedArtifactsRoot = stableRoot(input.artifactsRoot, 'artifacts-root-invalid')
  assertStrictDescendant(e2eRoot, requestedArtifactsRoot)
  const artifactsRoot = verifyOwnedArtifactsRoot(requestedArtifactsRoot)
  const lock = acquireStagingLock(artifactsRoot)
  let stagingRoot: string | undefined
  try {
    stagingRoot = createExclusiveStagingRoot(artifactsRoot)
    let redactionFailed = false
    for (const relativePath of CLASSIFIED_JSON) {
      const source = join(artifactsRoot, ...relativePath.split('/'))
      if (!existsSync(source)) continue
      const destination = join(stagingRoot, relativePath.replace(/[\\/]/g, '-'))
      try {
        const parsed: unknown = JSON.parse(readVerifiedSource(source, artifactsRoot).toString('utf8'))
        const sanitized = sanitizeLifecycleReport(parsed, input.roots)
        const serialized = `${JSON.stringify(sanitized, null, 2)}\n`
        if (containsSensitiveText(serialized)) throw new Error('redaction-failed')
        writeExclusiveStagedFile(destination, stagingRoot, artifactsRoot, serialized)
      } catch {
        removeSafeLeafIfPresent(destination, stagingRoot, artifactsRoot)
        redactionFailed = true
      }
    }

    verifyOwnedArtifactsRoot(artifactsRoot)
    const entries = readdirSync(artifactsRoot, { withFileTypes: true })
    verifyOwnedArtifactsRoot(artifactsRoot)
    for (const entry of entries) {
      if (!entry.isFile() || !CLASSIFIED_SCREENSHOT.test(entry.name)) continue
      const source = join(artifactsRoot, entry.name)
      const destination = join(stagingRoot, entry.name)
      try {
        const image = readVerifiedSource(source, artifactsRoot)
        writeExclusiveStagedFile(destination, stagingRoot, artifactsRoot, image)
      } catch {
        removeSafeLeafIfPresent(destination, stagingRoot, artifactsRoot)
        redactionFailed = true
      }
    }

    if (redactionFailed) {
      writeExclusiveStagedFile(
        join(stagingRoot, 'redaction-failed.json'),
        stagingRoot,
        artifactsRoot,
        `${JSON.stringify(FAILURE_RECORD, null, 2)}\n`,
      )
    }
    publishStagingRoot(stagingRoot, artifactsRoot)
    stagingRoot = undefined
  } finally {
    if (stagingRoot !== undefined) tryRemoveSafeTree(stagingRoot, artifactsRoot)
    releaseStagingLock(lock, artifactsRoot)
  }
}

function sanitizeValue(value: unknown, replacements: readonly RootReplacement[]): unknown {
  if (typeof value === 'string') return redactString(value, replacements)
  if (Array.isArray(value)) return value.map((entry) => sanitizeValue(entry, replacements))
  if (!isJsonObject(value)) return value

  const sanitized: JsonObject = {}
  for (const [key, entry] of Object.entries(value)) {
    if (FORBIDDEN_KEYS.test(key) || !ALLOWED_KEYS.has(key)) continue
    sanitized[key] = sanitizeValue(entry, replacements)
  }
  return sanitized
}

function buildRootReplacements(roots: RedactionRoots): RootReplacement[] {
  return [
    { token: '$DATA_ROOT', root: roots.dataRoot },
    { token: '$E2E_ROOT', root: roots.e2eRoot },
    { token: '$USER_HOME', root: roots.userHome },
    { token: '$TEMP', root: roots.temp },
  ]
    .map(({ token, root }) => rootReplacement(token, root))
    .filter((replacement): replacement is RootReplacement => replacement !== undefined)
    .sort((left, right) => right.sourceLength - left.sourceLength)
}

function rootReplacement(token: string, rawRoot: string): RootReplacement | undefined {
  if (typeof rawRoot !== 'string' || isUnsafeBroadRoot(rawRoot)) return undefined
  const root = normalizeExtendedWindowsPaths(rawRoot.trim()).replace(/[\\/]+$/, '')
  if (root === '') return undefined
  const { prefix, segments } = splitPath(root)
  if (segments.length === 0) return undefined
  const separator = '[\\\\/]+'
  const body = segments.map(escapeRegExp).join(separator)
  return {
    token,
    sourceLength: root.length,
    pattern: new RegExp(`${prefix}${body}(?=$|[\\\\/])`, 'gi'),
  }
}

function splitPath(path: string): { prefix: string; segments: string[] } {
  const segments = path.split(/[\\/]+/).filter(Boolean)
  if (/^[\\/]{2}/.test(path)) return { prefix: '[\\\\/]{2}', segments }
  if (/^[\\/]/.test(path)) return { prefix: '[\\\\/]+', segments }
  return { prefix: '', segments }
}

function redactString(value: string, replacements: readonly RootReplacement[]): string {
  let redacted = normalizeExtendedWindowsPaths(value)
  for (const replacement of replacements) redacted = redacted.replace(replacement.pattern, replacement.token)
  return redacted
}

function normalizeExtendedWindowsPaths(value: string): string {
  return value
    .replace(/[\\/]{2}\?[\\/]UNC[\\/]/gi, '\\\\')
    .replace(/[\\/]{2}\?[\\/]/gi, '')
}

function stableRoot(value: string | undefined, category: string): string {
  if (value === undefined || isUnsafeBroadRoot(value)) throw new Error(category)
  const target = resolve(normalizeExtendedWindowsPaths(value.trim()))
  if (!isAbsolute(target) || target === parse(target).root) throw new Error(category)
  return target
}

function isUnsafeBroadRoot(value: string): boolean {
  const trimmed = value.trim()
  return trimmed === '' || trimmed === '.' || trimmed === './' || trimmed === '.\\'
}

function containsSensitiveText(value: string): boolean {
  return SENSITIVE_TEXT.some((pattern) => pattern.test(value))
}

function assertContainedLeaf(path: string, root: string): string {
  const safeRoot = assertSafePath(root)
  const safePath = assertSafeLeaf(path)
  const relation = relative(safeRoot, safePath)
  if (relation === '' || relation === '..' || relation.startsWith(`..\\`) || relation.startsWith('../') || isAbsolute(relation)) {
    throw new Error('artifact-path-outside-root')
  }
  return safePath
}

function verifyControlledE2ERoot(path: string): string {
  try {
    const e2eRoot = assertSafePath(stableRoot(path, 'e2e-root-invalid'))
    if (!lstatSync(e2eRoot).isDirectory()) throw new Error('not-directory')
    return e2eRoot
  } catch {
    throw new Error('e2e-root-invalid')
  }
}

function assertStrictDescendant(root: string, path: string): void {
  const relation = relative(root, path)
  if (relation === '' || relation === '..' || relation.startsWith(`..\\`) || relation.startsWith('../') || isAbsolute(relation)) {
    throw new Error('artifacts-root-outside-e2e-root')
  }
}

function assertContainedOrEqual(root: string, path: string): void {
  const relation = relative(root, path)
  if (relation === '..' || relation.startsWith(`..\\`) || relation.startsWith('../') || isAbsolute(relation)) {
    throw new Error('artifacts-root-outside-e2e-root')
  }
}

function verifyOwnedArtifactsRoot(path: string): string {
  let descriptor: number | undefined
  try {
    const artifactsRoot = assertSafePath(path)
    if (!lstatSync(artifactsRoot).isDirectory()) throw new Error('not-directory')
    const marker = assertContainedLeaf(join(artifactsRoot, ARTIFACTS_OWNERSHIP_MARKER), artifactsRoot)
    const before = lstatSync(marker)
    if (!before.isFile()) throw new Error('marker-not-file')
    descriptor = openSync(marker, 'r')
    const opened = fstatSync(descriptor)
    if (!sameFile(before, opened) || !opened.isFile()) throw new Error('marker-replaced')
    const contents = readFileSync(descriptor, 'utf8')
    const afterRead = fstatSync(descriptor)
    const afterPath = lstatSync(assertContainedLeaf(marker, artifactsRoot))
    if (!sameFile(opened, afterRead) || !sameFile(afterRead, afterPath) || contents !== ARTIFACTS_OWNERSHIP_VALUE) {
      throw new Error('marker-invalid')
    }
    assertSafePath(artifactsRoot)
    return artifactsRoot
  } catch {
    throw new Error('artifacts-root-not-owned')
  } finally {
    if (descriptor !== undefined) closeSync(descriptor)
  }
}

function acquireStagingLock(artifactsRoot: string): string {
  verifyOwnedArtifactsRoot(artifactsRoot)
  const lock = assertContainedLeaf(join(artifactsRoot, '.upload-safe.lock'), artifactsRoot)
  try {
    mkdirSync(lock)
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'EEXIST') throw new Error('upload-safe-staging-locked')
    throw error
  }
  verifyOwnedArtifactsRoot(artifactsRoot)
  if (!lstatSync(assertSafePath(lock)).isDirectory()) throw new Error('upload-safe-staging-locked')
  return lock
}

function releaseStagingLock(lock: string, artifactsRoot: string): void {
  verifyOwnedArtifactsRoot(artifactsRoot)
  const safeLock = assertContainedLeaf(lock, artifactsRoot)
  if (!lstatSync(safeLock).isDirectory()) throw new Error('upload-safe-staging-lock-invalid')
  rmdirSync(safeLock)
  verifyOwnedArtifactsRoot(artifactsRoot)
}

function createExclusiveStagingRoot(artifactsRoot: string): string {
  verifyOwnedArtifactsRoot(artifactsRoot)
  const stagingRoot = mkdtempSync(join(artifactsRoot, '.upload-safe-stage-'))
  verifyOwnedArtifactsRoot(artifactsRoot)
  const safeStagingRoot = assertContainedLeaf(stagingRoot, artifactsRoot)
  if (!lstatSync(safeStagingRoot).isDirectory()) throw new Error('upload-safe-stage-invalid')
  return safeStagingRoot
}

function readVerifiedSource(path: string, artifactsRoot: string): Buffer {
  verifyOwnedArtifactsRoot(artifactsRoot)
  const source = assertContainedLeaf(path, artifactsRoot)
  const before = lstatSync(source)
  if (!before.isFile()) throw new Error('artifact-source-invalid')
  let descriptor: number | undefined
  try {
    descriptor = openSync(source, 'r')
    const opened = fstatSync(descriptor)
    if (!sameFile(before, opened) || !opened.isFile()) throw new Error('artifact-source-replaced')
    const value = readFileSync(descriptor)
    const afterRead = fstatSync(descriptor)
    const afterPath = lstatSync(assertContainedLeaf(source, artifactsRoot))
    if (!sameFile(opened, afterRead) || !sameFile(afterRead, afterPath)) throw new Error('artifact-source-replaced')
    verifyOwnedArtifactsRoot(artifactsRoot)
    return value
  } finally {
    if (descriptor !== undefined) closeSync(descriptor)
  }
}

function writeExclusiveStagedFile(
  path: string,
  stagingRoot: string,
  artifactsRoot: string,
  value: string | Buffer,
): void {
  verifyOwnedArtifactsRoot(artifactsRoot)
  assertSafePath(stagingRoot)
  const destination = assertContainedLeaf(path, stagingRoot)
  writeFileSync(destination, value, { flag: 'wx' })
  const written = assertContainedLeaf(destination, stagingRoot)
  if (!lstatSync(written).isFile()) throw new Error('upload-safe-write-invalid')
  verifyOwnedArtifactsRoot(artifactsRoot)
}

function removeSafeLeafIfPresent(path: string, stagingRoot: string, artifactsRoot: string): void {
  if (!existsSync(path)) return
  verifyOwnedArtifactsRoot(artifactsRoot)
  assertSafePath(stagingRoot)
  const destination = assertContainedLeaf(path, stagingRoot)
  if (!lstatSync(destination).isFile()) throw new Error('upload-safe-leaf-invalid')
  unlinkSync(destination)
  verifyOwnedArtifactsRoot(artifactsRoot)
}

function publishStagingRoot(stagingRoot: string, artifactsRoot: string): void {
  verifyOwnedArtifactsRoot(artifactsRoot)
  assertSafeTree(stagingRoot, artifactsRoot)
  const uploadSafe = assertContainedLeaf(join(artifactsRoot, 'upload-safe'), artifactsRoot)
  let retiredRoot: string | undefined
  if (existsSync(uploadSafe)) {
    assertSafeTree(uploadSafe, artifactsRoot)
    retiredRoot = assertContainedLeaf(join(artifactsRoot, `.upload-safe-retired-${randomUUID()}`), artifactsRoot)
    verifyOwnedArtifactsRoot(artifactsRoot)
    renameSync(uploadSafe, retiredRoot)
    verifyOwnedArtifactsRoot(artifactsRoot)
    assertSafeTree(retiredRoot, artifactsRoot)
  }

  try {
    assertContainedLeaf(uploadSafe, artifactsRoot)
    assertSafeTree(stagingRoot, artifactsRoot)
    verifyOwnedArtifactsRoot(artifactsRoot)
    renameSync(stagingRoot, uploadSafe)
    verifyOwnedArtifactsRoot(artifactsRoot)
    assertSafeTree(uploadSafe, artifactsRoot)
  } catch (error) {
    if (retiredRoot !== undefined && existsSync(retiredRoot) && !existsSync(uploadSafe)) {
      assertSafeTree(retiredRoot, artifactsRoot)
      renameSync(retiredRoot, uploadSafe)
    }
    throw error
  }

  if (retiredRoot !== undefined) removeSafeTree(retiredRoot, artifactsRoot)
}

function assertSafeTree(path: string, artifactsRoot: string): void {
  verifyOwnedArtifactsRoot(artifactsRoot)
  const target = assertContainedLeaf(path, artifactsRoot)
  const stat = lstatSync(target)
  if (stat.isSymbolicLink()) throw new Error('upload-safe-tree-reparse')
  if (!stat.isDirectory()) return
  for (const entry of readdirSync(target)) assertSafeTree(join(target, entry), artifactsRoot)
  verifyOwnedArtifactsRoot(artifactsRoot)
}

function removeSafeTree(path: string, artifactsRoot: string): void {
  assertSafeTree(path, artifactsRoot)
  removeSafeTreeEntries(path, artifactsRoot)
}

function removeSafeTreeEntries(path: string, artifactsRoot: string): void {
  verifyOwnedArtifactsRoot(artifactsRoot)
  const target = assertContainedLeaf(path, artifactsRoot)
  const stat = lstatSync(target)
  if (stat.isSymbolicLink()) throw new Error('upload-safe-tree-reparse')
  if (stat.isDirectory()) {
    for (const entry of readdirSync(target)) removeSafeTreeEntries(join(target, entry), artifactsRoot)
    verifyOwnedArtifactsRoot(artifactsRoot)
    const emptyDirectory = assertContainedLeaf(target, artifactsRoot)
    if (!lstatSync(emptyDirectory).isDirectory()) throw new Error('upload-safe-tree-replaced')
    rmdirSync(emptyDirectory)
  } else {
    unlinkSync(target)
  }
  verifyOwnedArtifactsRoot(artifactsRoot)
}

function tryRemoveSafeTree(path: string, artifactsRoot: string): void {
  try {
    if (existsSync(path)) removeSafeTree(path, artifactsRoot)
  } catch {
    // A concurrently replaced/reparse staging tree is intentionally left untouched.
  }
}

function sameFile(left: Stats, right: Stats): boolean {
  return left.dev === right.dev
    && left.ino === right.ino
    && left.size === right.size
    && left.mtimeMs === right.mtimeMs
}

function isJsonObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function firstNonBlank(...values: Array<string | undefined>): string | undefined {
  return values.find((value) => value !== undefined && value.trim() !== '')
}
