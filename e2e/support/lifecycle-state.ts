import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { isAbsolute, join, relative, resolve } from 'node:path'

export interface LifecycleSnapshot {
  runtime: { version: string; activeDirToken: string }
  profile: { selectedId: string; lastKnownGoodId: string; revision: number; pending: boolean }
  project: { workspaceId: string; pathToken: string; sentinelSha256: string }
  sessionIds: string[]
}

interface JsonObject {
  [key: string]: unknown
}

interface SelectedProfile {
  id: string
  dataRoot: string
}

interface ProfileEvidence {
  selected: SelectedProfile
  selectedId: string
  lastKnownGoodId: string
  revision: number
  pending: boolean
}

interface WorkspaceRecord {
  workspaceId: string
  path: string
  value: JsonObject
}

const COMPARISONS: ReadonlyArray<{
  category: string
  changed: (before: LifecycleSnapshot, after: LifecycleSnapshot) => boolean
}> = [
  { category: 'runtime-version-changed', changed: (before, after) => before.runtime.version !== after.runtime.version },
  { category: 'runtime-active-dir-changed', changed: (before, after) => before.runtime.activeDirToken !== after.runtime.activeDirToken },
  { category: 'profile-selected-changed', changed: (before, after) => before.profile.selectedId !== after.profile.selectedId },
  { category: 'profile-last-known-good-changed', changed: (before, after) => before.profile.lastKnownGoodId !== after.profile.lastKnownGoodId },
  { category: 'profile-revision-changed', changed: (before, after) => before.profile.revision !== after.profile.revision },
  { category: 'profile-pending', changed: (before, after) => before.profile.pending !== after.profile.pending },
  { category: 'project-workspace-changed', changed: (before, after) => before.project.workspaceId !== after.project.workspaceId },
  { category: 'project-path-changed', changed: (before, after) => before.project.pathToken !== after.project.pathToken },
  { category: 'project-sentinel-changed', changed: (before, after) => before.project.sentinelSha256 !== after.project.sentinelSha256 },
  { category: 'session-ids-changed', changed: (before, after) => !sameStrings(before.sessionIds, after.sessionIds) },
]

export function captureLifecycleSnapshot(input: {
  dataRoot: string
  projectPath: string
  roots: { dataRoot: string; e2eRoot: string; userHome: string; temp: string }
}): LifecycleSnapshot {
  const dataRoot = absolutePath(input.dataRoot, 'data-root-invalid')
  const receipt = expectObject(readJson(join(dataRoot, 'state', 'provisioning.json')), 'provisioning-invalid')
  const profile = readProfileEvidence(dataRoot)
  const workspaces = readRegisteredWorkspaces(profile.selected.dataRoot)
  const projectPath = absolutePath(input.projectPath, 'project-path-invalid')
  const matching = workspaces.filter((workspace) => samePath(workspace.path, projectPath))
  if (matching.length !== 1) throw new Error('workspace-not-registered')
  const workspace = matching[0]
  const sessionIds = expectStringArray(workspace.value.sessionIds, 'workspace-session-ids-invalid').sort()
  const runtimeVersion = expectString(receipt.runtimeVersion, 'provisioning-invalid')
  const activeDir = expectString(receipt.activeDir, 'provisioning-invalid')
  const sentinelSha256 = readSha256(join(projectPath, 'e2e-preserve.txt'))

  return {
    runtime: {
      version: runtimeVersion,
      activeDirToken: tokenizePath(activeDir, input.roots),
    },
    profile: {
      selectedId: profile.selectedId,
      lastKnownGoodId: profile.lastKnownGoodId,
      revision: profile.revision,
      pending: profile.pending,
    },
    project: {
      workspaceId: workspace.workspaceId,
      pathToken: tokenizePath(projectPath, input.roots),
      sentinelSha256,
    },
    sessionIds,
  }
}

export function compareUpgradeState(before: LifecycleSnapshot, after: LifecycleSnapshot): string[] {
  return COMPARISONS.filter((comparison) => comparison.changed(before, after)).map((comparison) => comparison.category)
}

export function captureProjectPath(dataRoot: string): string {
  const e2eRoot = process.env.DSH_E2E_ROOT
  if (e2eRoot === undefined || e2eRoot.trim() === '') {
    throw new Error('e2e-root-missing')
  }
  const projectsOwned = join(absolutePath(e2eRoot, 'e2e-root-missing'), 'projects-owned')
  const profile = readProfileEvidence(dataRoot)
  const matches = readRegisteredWorkspaces(profile.selected.dataRoot)
    .map((workspace) => absolutePath(workspace.path, 'workspace-path-invalid'))
    .filter((workspacePath) => isStrictDescendant(projectsOwned, workspacePath))
  if (matches.length !== 1) throw new Error('e2e-workspace-count')
  return matches[0]
}

function readProfileEvidence(dataRoot: string): ProfileEvidence {
  const profileRegistryRoot = absolutePath(dataRoot, 'data-root-invalid')
  const profilesValue = readJson(join(profileRegistryRoot, 'profiles', 'profiles.json'))
  if (!Array.isArray(profilesValue)) throw new Error('profiles-invalid')
  const state = expectObject(readJson(join(profileRegistryRoot, 'profiles', 'state.json')), 'profile-state-invalid')
  const selectedProfile = expectObject(state.selectedProfile, 'profile-state-invalid')
  const selectedId = expectString(selectedProfile.profileId, 'profile-state-invalid')
  const revision = expectInteger(selectedProfile.revision, 'profile-state-invalid')
  const selectedValue = profilesValue.find((profile) => isObject(profile) && profile.id === selectedId)
  if (!isObject(selectedValue)) throw new Error('selected-profile-missing')
  const selected: SelectedProfile = {
    id: expectString(selectedValue.id, 'selected-profile-invalid'),
    dataRoot: expectString(selectedValue.dataRoot, 'selected-profile-invalid'),
  }
  const lastKnownGood = expectObject(state.lastKnownGood, 'profile-state-invalid')
  const pendingValue = state.pending
  if (pendingValue !== null && pendingValue !== undefined && !isObject(pendingValue)) {
    throw new Error('profile-state-invalid')
  }
  return {
    selected,
    selectedId,
    lastKnownGoodId: expectString(lastKnownGood.profileId, 'profile-state-invalid'),
    revision,
    pending: pendingValue !== null && pendingValue !== undefined,
  }
}

function readRegisteredWorkspaces(profileDataRoot: string): WorkspaceRecord[] {
  const profileRoot = absolutePath(profileDataRoot, 'selected-profile-invalid')
  const storage = expectObject(readJson(join(profileRoot, 'storages', 'workspace.json')), 'workspace-registry-invalid')
  const global = expectObject(storage.global, 'workspace-registry-invalid')
  const tables = expectObject(storage.tables, 'workspace-registry-invalid')
  const workspaces = expectObject(tables.workspaces, 'workspace-registry-invalid')
  const workspaceIds = expectStringArray(global.workspaceIds, 'workspace-registry-invalid')
  return workspaceIds.map((workspaceId) => {
    const value = expectObject(workspaces[workspaceId], 'workspace-registry-invalid')
    return {
      workspaceId,
      path: expectString(value.path, 'workspace-path-invalid'),
      value,
    }
  })
}

function readJson(path: string): JsonObject | unknown[] {
  let source: string
  try {
    source = readFileSync(path, 'utf8')
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') throw new Error('json-missing')
    throw new Error('json-unreadable')
  }
  if (source.trim() === '') throw new Error('json-empty')
  let value: unknown
  try {
    value = JSON.parse(source)
  } catch {
    throw new Error('json-invalid')
  }
  if (!isObject(value) && !Array.isArray(value)) throw new Error('json-non-object')
  return value
}

function tokenizePath(
  path: string,
  roots: { dataRoot: string; e2eRoot: string; userHome: string; temp: string },
): string {
  const target = absolutePath(path, 'path-token-invalid')
  const candidates = [
    { token: '$DATA_ROOT', path: absolutePath(roots.dataRoot, 'path-token-invalid') },
    { token: '$E2E_ROOT', path: absolutePath(roots.e2eRoot, 'path-token-invalid') },
    { token: '$USER_HOME', path: absolutePath(roots.userHome, 'path-token-invalid') },
    { token: '$TEMP', path: absolutePath(roots.temp, 'path-token-invalid') },
  ].sort((left, right) => right.path.length - left.path.length)
  for (const candidate of candidates) {
    if (samePath(candidate.path, target)) return candidate.token
    if (isStrictDescendant(candidate.path, target)) {
      return `${candidate.token}/${relative(candidate.path, target).replaceAll('\\', '/')}`
    }
  }
  return `$UNSCOPED/${createHash('sha256').update(pathKey(target)).digest('hex')}`
}

function readSha256(path: string): string {
  try {
    return createHash('sha256').update(readFileSync(path)).digest('hex')
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') throw new Error('project-sentinel-missing')
    throw new Error('project-sentinel-unreadable')
  }
}

function expectObject(value: unknown, category: string): JsonObject {
  if (!isObject(value)) throw new Error(category)
  return value
}

function expectString(value: unknown, category: string): string {
  if (typeof value !== 'string' || value.trim() === '') throw new Error(category)
  return value
}

function expectInteger(value: unknown, category: string): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw new Error(category)
  return value
}

function expectStringArray(value: unknown, category: string): string[] {
  if (!Array.isArray(value) || value.some((entry) => typeof entry !== 'string' || entry.trim() === '')) {
    throw new Error(category)
  }
  return [...value]
}

function isObject(value: unknown): value is JsonObject {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function absolutePath(path: string, category: string): string {
  const normalized = normalizeWindowsExtendedPath(path)
  if (!isAbsolute(normalized)) throw new Error(category)
  return resolve(normalized)
}

function samePath(left: string, right: string): boolean {
  return pathKey(absolutePath(left, 'workspace-path-invalid')) === pathKey(absolutePath(right, 'workspace-path-invalid'))
}

function isStrictDescendant(root: string, target: string): boolean {
  const normalizedRoot = absolutePath(root, 'path-comparison-invalid')
  const normalizedTarget = absolutePath(target, 'path-comparison-invalid')
  const rel = relative(normalizedRoot, normalizedTarget)
  return rel !== '' && rel !== '..' && !rel.startsWith(`..\\`) && !rel.startsWith('../') && !isAbsolute(rel)
}

function pathKey(path: string): string {
  const normalized = absolutePath(path, 'path-comparison-invalid')
  return process.platform === 'win32' ? normalized.toLowerCase() : normalized
}

function normalizeWindowsExtendedPath(path: string): string {
  if (/^\\\\\?\\UNC\\/i.test(path)) return `\\\\${path.slice(8)}`
  if (/^\\\\\?\\(?=[A-Za-z]:[\\/])/.test(path)) return path.slice(4)
  return path
}

function sameStrings(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index])
}
