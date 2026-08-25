import { createHash } from 'node:crypto'
import { mkdirSync, mkdtempSync, rmSync, unlinkSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { tmpdir } from 'node:os'
import { afterEach, describe, expect, it } from 'vitest'
import {
  captureLifecycleSnapshot,
  captureProjectPath,
  compareUpgradeState,
  type LifecycleSnapshot,
} from './lifecycle-state'

const temporaryRoots: string[] = []
const originalE2eRoot = process.env.DSH_E2E_ROOT

afterEach(() => {
  if (originalE2eRoot === undefined) delete process.env.DSH_E2E_ROOT
  else process.env.DSH_E2E_ROOT = originalE2eRoot
  for (const root of temporaryRoots.splice(0)) rmSync(root, { recursive: true, force: true })
})

describe('compareUpgradeState', () => {
  it('returns no violations when both snapshots are identical', () => {
    const snapshot = exampleSnapshot()

    expect(compareUpgradeState(snapshot, structuredClone(snapshot))).toEqual([])
  })

  it('is reflexive when the snapshot already has a pending profile', () => {
    const snapshot = exampleSnapshot()
    snapshot.profile.pending = true

    expect(compareUpgradeState(snapshot, structuredClone(snapshot))).toEqual([])
  })

  it('reports profile pending and changed session ids in stable order', () => {
    const before = exampleSnapshot()
    const after = structuredClone(before)
    after.profile.pending = true
    after.sessionIds.pop()

    expect(compareUpgradeState(before, after)).toEqual(['profile-pending', 'session-ids-changed'])
  })

  it('covers every persisted upgrade invariant without serializing snapshots', () => {
    const before = exampleSnapshot()
    const after = structuredClone(before)
    after.runtime.version = '1.8.3'
    after.runtime.activeDirToken = '$DATA_ROOT/runtime/candidates/1.8.3'
    after.profile.selectedId = 'profile-other'
    after.profile.lastKnownGoodId = 'profile-other'
    after.profile.revision = 8
    after.profile.pending = true
    after.project.workspaceId = 'workspace-other'
    after.project.pathToken = '$E2E_ROOT/projects-owned/project-b'
    after.project.sentinelSha256 = 'b'.repeat(64)
    after.sessionIds = ['session-other']

    expect(compareUpgradeState(before, after)).toEqual([
      'runtime-version-changed',
      'runtime-active-dir-changed',
      'profile-selected-changed',
      'profile-last-known-good-changed',
      'profile-revision-changed',
      'profile-pending',
      'project-workspace-changed',
      'project-path-changed',
      'project-sentinel-changed',
      'session-ids-changed',
    ])
  })
})

describe('captureLifecycleSnapshot', () => {
  it('reads lifecycle evidence, tokenizes paths, hashes the project sentinel, and sorts sessions', () => {
    const fixture = createFixture()

    const snapshot = captureLifecycleSnapshot({
      dataRoot: fixture.dataRoot,
      projectPath: fixture.projectPath,
      roots: fixture.roots,
    })

    expect(snapshot).toEqual({
      runtime: {
        version: '1.8.2',
        activeDirToken: '$DATA_ROOT/runtime/candidates/1.8.2',
      },
      profile: {
        selectedId: 'profile-selected',
        lastKnownGoodId: 'profile-selected',
        revision: 7,
        pending: false,
      },
      project: {
        workspaceId: 'workspace-project',
        pathToken: '$E2E_ROOT/projects-owned/project-a',
        sentinelSha256: sha256('preserve this project'),
      },
      sessionIds: ['session-a', 'session-z'],
    })
    expect(JSON.stringify(snapshot)).not.toContain(fixture.root)
  })

  it('accepts the persisted profile shape when pending is omitted', () => {
    const fixture = createFixture()
    writeJson(fixture.stateFile, {
      selectedProfile: { profileId: 'profile-selected', revision: 7 },
      lastKnownGood: { profileId: 'profile-selected', revision: 7, runtimeVersion: '1.8.2' },
    })

    expect(captureLifecycleSnapshot({
      dataRoot: fixture.dataRoot,
      projectPath: fixture.projectPath,
      roots: fixture.roots,
    }).profile).toEqual({
      selectedId: 'profile-selected',
      lastKnownGoodId: 'profile-selected',
      revision: 7,
      pending: false,
    })
  })

  it.runIf(process.platform === 'win32')('normalizes extended drive paths before tokenization and comparison', () => {
    const fixture = createFixture()
    const normal = captureLifecycleSnapshot({
      dataRoot: fixture.dataRoot,
      projectPath: fixture.projectPath,
      roots: fixture.roots,
    })
    writeJson(fixture.provisioningFile, {
      runtimeVersion: '1.8.2',
      activeDir: `\\\\?\\${join(fixture.dataRoot, 'runtime', 'candidates', '1.8.2')}`,
    })

    const extended = captureLifecycleSnapshot({
      dataRoot: fixture.dataRoot,
      projectPath: `\\\\?\\${fixture.projectPath}`,
      roots: fixture.roots,
    })

    expect(extended.runtime.activeDirToken).toBe('$DATA_ROOT/runtime/candidates/1.8.2')
    expect(compareUpgradeState(normal, extended)).toEqual([])
  })

  it.runIf(process.platform === 'win32')('normalizes extended UNC paths against ordinary UNC roots', () => {
    const fixture = createFixture()
    writeJson(fixture.provisioningFile, {
      runtimeVersion: '1.8.2',
      activeDir: '\\\\?\\UNC\\server\\share\\data\\runtime\\candidate',
    })

    const snapshot = captureLifecycleSnapshot({
      dataRoot: fixture.dataRoot,
      projectPath: fixture.projectPath,
      roots: { ...fixture.roots, dataRoot: '\\\\server\\share\\data' },
    })

    expect(snapshot.runtime.activeDirToken).toBe('$DATA_ROOT/runtime/candidate')
  })

  it('rejects a workspace table entry that is not formally registered', () => {
    const fixture = createFixture()
    writeJson(fixture.workspaceFile, {
      global: { workspaceIds: ['workspace-other'] },
      tables: {
        workspaces: {
          'workspace-project': { path: fixture.projectPath, sessionIds: [] },
          'workspace-other': { path: join(fixture.root, 'outside'), sessionIds: [] },
        },
      },
    })

    expect(() => captureLifecycleSnapshot({
      dataRoot: fixture.dataRoot,
      projectPath: fixture.projectPath,
      roots: fixture.roots,
    })).toThrow('workspace-not-registered')
  })

  describe.each([
    ['provisioning.json', 'provisioningFile'],
    ['profiles.json', 'profilesFile'],
    ['state.json', 'stateFile'],
    ['workspace.json', 'workspaceFile'],
  ] as const)('%s validation', (_fileName, fileKey) => {
    it.each([
      ['missing', (path: string) => unlinkSync(path), 'json-missing'],
      ['empty', (path: string) => writeFileSync(path, '', 'utf8'), 'json-empty'],
      ['non-object', (path: string) => writeFileSync(path, '"raw-json-must-not-leak"', 'utf8'), 'json-non-object'],
    ])('rejects %s input without leaking paths or JSON', (_caseName, mutate, category) => {
      const fixture = createFixture()
      mutate(fixture[fileKey])

      expectStableError(() => captureLifecycleSnapshot({
        dataRoot: fixture.dataRoot,
        projectPath: fixture.projectPath,
        roots: fixture.roots,
      }), category, fixture.root)
    })
  })

  it('rejects invalid JSON without leaking its source', () => {
    const fixture = createFixture()
    writeFileSync(fixture.provisioningFile, '{ raw-json-must-not-leak', 'utf8')

    expectStableError(() => captureLifecycleSnapshot({
      dataRoot: fixture.dataRoot,
      projectPath: fixture.projectPath,
      roots: fixture.roots,
    }), 'json-invalid', fixture.root)
  })
})

describe('captureProjectPath', () => {
  it('returns the sole formally registered project below E2E projects-owned', () => {
    const fixture = createFixture()
    process.env.DSH_E2E_ROOT = fixture.roots.e2eRoot

    expect(captureProjectPath(fixture.dataRoot)).toBe(resolve(fixture.projectPath))
  })

  it('rejects zero E2E workspaces', () => {
    const fixture = createFixture()
    process.env.DSH_E2E_ROOT = fixture.roots.e2eRoot
    writeJson(fixture.workspaceFile, {
      global: { workspaceIds: ['workspace-outside'] },
      tables: { workspaces: { 'workspace-outside': { path: join(fixture.root, 'outside'), sessionIds: [] } } },
    })

    expect(() => captureProjectPath(fixture.dataRoot)).toThrow('e2e-workspace-count')
  })

  it('rejects multiple E2E workspaces', () => {
    const fixture = createFixture()
    process.env.DSH_E2E_ROOT = fixture.roots.e2eRoot
    const second = join(fixture.roots.e2eRoot, 'projects-owned', 'project-b')
    mkdirSync(second, { recursive: true })
    writeJson(fixture.workspaceFile, {
      global: { workspaceIds: ['workspace-project', 'workspace-second'] },
      tables: {
        workspaces: {
          'workspace-project': { path: fixture.projectPath, sessionIds: [] },
          'workspace-second': { path: second, sessionIds: [] },
        },
      },
    })

    expect(() => captureProjectPath(fixture.dataRoot)).toThrow('e2e-workspace-count')
  })
})

function exampleSnapshot(): LifecycleSnapshot {
  return {
    runtime: { version: '1.8.2', activeDirToken: '$DATA_ROOT/runtime/candidates/1.8.2' },
    profile: {
      selectedId: 'profile-selected',
      lastKnownGoodId: 'profile-selected',
      revision: 7,
      pending: false,
    },
    project: {
      workspaceId: 'workspace-project',
      pathToken: '$E2E_ROOT/projects-owned/project-a',
      sentinelSha256: 'a'.repeat(64),
    },
    sessionIds: ['session-a', 'session-z'],
  }
}

function createFixture() {
  const root = mkdtempSync(join(tmpdir(), 'dsh-lifecycle-'))
  temporaryRoots.push(root)
  const dataRoot = join(root, 'app-data')
  const e2eRoot = join(root, 'e2e')
  const profileRoot = join(dataRoot, 'profiles', 'selected-data')
  const projectPath = join(e2eRoot, 'projects-owned', 'project-a')
  const provisioningFile = join(dataRoot, 'state', 'provisioning.json')
  const profilesFile = join(dataRoot, 'profiles', 'profiles.json')
  const stateFile = join(dataRoot, 'profiles', 'state.json')
  const workspaceFile = join(profileRoot, 'storages', 'workspace.json')
  const roots = {
    dataRoot,
    e2eRoot,
    userHome: join(root, 'user-home'),
    temp: join(root, 'temp'),
  }

  mkdirSync(join(dataRoot, 'state'), { recursive: true })
  mkdirSync(join(dataRoot, 'profiles'), { recursive: true })
  mkdirSync(join(profileRoot, 'storages'), { recursive: true })
  mkdirSync(projectPath, { recursive: true })
  writeJson(provisioningFile, {
    runtimeVersion: '1.8.2',
    activeDir: join(dataRoot, 'runtime', 'candidates', '1.8.2'),
  })
  writeJson(profilesFile, [
    { id: 'profile-selected', dataRoot: profileRoot, revision: 7 },
    { id: 'profile-other', dataRoot: join(dataRoot, 'profiles', 'other-data'), revision: 2 },
  ])
  writeJson(stateFile, {
    selectedProfile: { profileId: 'profile-selected', revision: 7 },
    pending: null,
    lastKnownGood: { profileId: 'profile-selected', revision: 7, runtimeVersion: '1.8.2' },
  })
  writeJson(workspaceFile, {
    global: { workspaceIds: ['workspace-project'] },
    tables: {
      workspaces: {
        'workspace-project': {
          path: projectPath,
          sessionIds: ['session-z', 'session-a'],
        },
        'workspace-unregistered': {
          path: projectPath,
          sessionIds: ['session-ignored'],
        },
      },
    },
  })
  writeFileSync(join(projectPath, 'e2e-preserve.txt'), 'preserve this project', 'utf8')

  return {
    root,
    dataRoot,
    projectPath,
    provisioningFile,
    profilesFile,
    stateFile,
    workspaceFile,
    roots,
  }
}

function writeJson(path: string, value: unknown): void {
  mkdirSync(resolve(path, '..'), { recursive: true })
  writeFileSync(path, JSON.stringify(value, null, 2), 'utf8')
}

function sha256(value: string): string {
  return createHash('sha256').update(value).digest('hex')
}

function expectStableError(action: () => unknown, category: string, rawRoot: string): void {
  let thrown: unknown
  try {
    action()
  } catch (error) {
    thrown = error
  }
  expect(thrown).toBeInstanceOf(Error)
  expect((thrown as Error).message).toBe(category)
  expect((thrown as Error).message).not.toContain(rawRoot)
  expect((thrown as Error).message).not.toContain('raw-json-must-not-leak')
}
