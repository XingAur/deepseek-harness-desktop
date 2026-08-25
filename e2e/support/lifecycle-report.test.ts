import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import {
  initializeE2EArtifactsRoot,
  lifecycleRedactionRoots,
  recordLifecycleReport,
  sanitizeLifecycleReport,
  stageSafeLifecycleArtifacts,
  type RedactionRoots,
} from './lifecycle-report'

const temporaryRoots: string[] = []
const originalE2eRoot = process.env.DSH_E2E_ROOT

afterEach(() => {
  if (originalE2eRoot === undefined) delete process.env.DSH_E2E_ROOT
  else process.env.DSH_E2E_ROOT = originalE2eRoot
  for (const root of temporaryRoots.splice(0)) rmSync(root, { recursive: true, force: true })
})

describe('sanitizeLifecycleReport', () => {
  it('keeps only allowed keys, recursively redacts arrays, and removes conversation secrets', () => {
    const roots: RedactionRoots = {
      dataRoot: 'C:\\Users\\Alice\\AppData\\Local\\DeepSeek Harness',
      e2eRoot: 'C:\\Users\\Alice\\work\\e2e',
      userHome: 'C:\\Users\\Alice',
      temp: 'C:\\Users\\Alice\\AppData\\Local\\Temp',
    }

    const result = sanitizeLifecycleReport({
      schemaVersion: 1,
      stage: 'upgrade',
      path: '\\\\?\\c:/users/ALICE/AppData/Local/DeepSeek Harness\\state.json',
      apiKey: 'sk-secret',
      prompt: 'private prompt',
      response: 'private response',
      messages: ['private message'],
      unknownBody: 'private conversation body',
      snapshot: [
        { path: 'C:/Users/Alice/AppData/Local/Temp/capture.json', cookie: 'session=private' },
        'c:\\users\\alice\\notes.txt',
      ],
    }, roots)

    expect(result).toEqual({
      schemaVersion: 1,
      stage: 'upgrade',
      path: '$DATA_ROOT\\state.json',
      snapshot: [
        { path: '$TEMP/capture.json' },
        '$USER_HOME\\notes.txt',
      ],
    })
    const serialized = JSON.stringify(result)
    expect(serialized.toLowerCase()).not.toContain('alice')
    expect(serialized).not.toContain('sk-secret')
    expect(serialized).not.toContain('private prompt')
    expect(serialized).not.toContain('private response')
    expect(serialized).not.toContain('private message')
    expect(serialized).not.toContain('private conversation body')
  })

  it('prefers the longest overlapping root and handles extended UNC paths case-insensitively', () => {
    const roots: RedactionRoots = {
      dataRoot: '\\\\server\\share\\users\\alice\\data',
      e2eRoot: '\\\\server\\share\\e2e',
      userHome: '\\\\server\\share\\users\\alice',
      temp: '\\\\server\\share\\users\\alice\\temp',
    }

    expect(sanitizeLifecycleReport({
      path: '\\\\?\\UNC\\SERVER\\SHARE\\users\\ALICE\\data/State.json',
      artifactRoot: '\\\\server/share/users/alice/report',
    }, roots)).toEqual({
      path: '$DATA_ROOT/State.json',
      artifactRoot: '$USER_HOME/report',
    })
  })

  it.each([
    ['all backslashes', '\\\\?\\UNC\\SERVER\\SHARE\\Users\\ALICE\\data\\State.json', '$DATA_ROOT\\State.json'],
    ['mixed separators', '\\\\?\\UNC/SERVER/SHARE/Users/ALICE/data/State.json', '$DATA_ROOT/State.json'],
    ['all forward slashes', '//?/UNC/SERVER/SHARE/Users/ALICE/data/State.json', '$DATA_ROOT/State.json'],
  ])('redacts %s extended UNC paths without leaking the user name', (_caseName, path, expected) => {
    const result = sanitizeLifecycleReport({ path }, {
      dataRoot: '\\\\server\\share\\Users\\Alice\\data',
      e2eRoot: '\\\\server\\share\\e2e',
      userHome: '\\\\server\\share\\Users\\Alice',
      temp: '\\\\server\\share\\Users\\Alice\\temp',
    })

    expect(result).toEqual({ path: expected })
    expect(JSON.stringify(result).toLowerCase()).not.toContain('alice')
  })

  it('does not turn empty or dot roots into broad replacement patterns', () => {
    process.env.DSH_E2E_ROOT = '.'

    expect(() => lifecycleRedactionRoots('')).toThrow('data-root-invalid')
    expect(() => lifecycleRedactionRoots('.')).toThrow('data-root-invalid')
    expect(() => lifecycleRedactionRoots(resolve('valid-data-root'))).toThrow('e2e-root-invalid')
  })
})

describe('stageSafeLifecycleArtifacts', () => {
  it('rejects an ordinary unowned directory before touching its existing upload-safe content', () => {
    const root = createTemporaryRoot('dsh-unowned-artifacts-')
    const e2eRoot = join(root, 'e2e')
    const artifactsRoot = join(e2eRoot, 'ordinary-directory')
    const uploadSafe = join(artifactsRoot, 'upload-safe')
    const sentinel = join(uploadSafe, 'must-survive.txt')
    mkdirSync(uploadSafe, { recursive: true })
    writeFileSync(sentinel, 'not-owned', 'utf8')

    expect(() => stageSafeLifecycleArtifacts({
      artifactsRoot,
      roots: {
        dataRoot: join(root, 'data'),
        e2eRoot,
        userHome: join(root, 'home'),
        temp: join(root, 'temp'),
      },
    })).toThrow('artifacts-root-not-owned')
    expect(readFileSync(sentinel, 'utf8')).toBe('not-owned')
  })

  it('rejects a manually marked artifacts root outside e2eRoot when stage is called directly', () => {
    const root = createTemporaryRoot('dsh-outside-artifacts-')
    const e2eRoot = join(root, 'e2e')
    const artifactsRoot = join(root, 'outside')
    const uploadSafe = join(artifactsRoot, 'upload-safe')
    const sentinel = join(uploadSafe, 'must-survive.txt')
    mkdirSync(e2eRoot, { recursive: true })
    mkdirSync(uploadSafe, { recursive: true })
    writeFileSync(join(artifactsRoot, '.dsh-e2e-artifacts-owned'), 'E2E-owned', 'utf8')
    writeFileSync(sentinel, 'outside', 'utf8')

    expect(() => stageSafeLifecycleArtifacts({
      artifactsRoot,
      roots: { dataRoot: join(root, 'data'), e2eRoot, userHome: join(root, 'home'), temp: join(root, 'temp') },
    })).toThrow('artifacts-root-outside-e2e-root')
    expect(readFileSync(sentinel, 'utf8')).toBe('outside')
  })

  it('stages only four classified JSON files and fixed lifecycle screenshots', () => {
    const fixture = createArtifactsFixture()
    writeJson(join(fixture.artifactsRoot, 'lifecycle-report.json'), safeReport('report', fixture.roots))
    writeJson(join(fixture.artifactsRoot, 'instrumented-setup.json'), safeReport('setup', fixture.roots))
    writeJson(join(fixture.artifactsRoot, 'installer-records', 'latest-install.json'), safeReport('install', fixture.roots))
    writeJson(join(fixture.artifactsRoot, 'generation-timeline.json'), safeReport('timeline', fixture.roots))
    writeJson(join(fixture.artifactsRoot, 'unclassified.json'), safeReport('unclassified', fixture.roots))
    writeFileSync(join(fixture.artifactsRoot, 'webdriver-backend.log'), 'Authorization: Bearer secret', 'utf8')
    writeFileSync(join(fixture.artifactsRoot, 'runtime-ca-private.pem'), 'PRIVATE KEY', 'utf8')
    for (const name of [
      'quick-baseline-final.png',
      'full-candidate-failure.png',
      'quick-preserve-all-final.png',
      'full-delete-app-data-failure.png',
      'quick-delete-all-final.png',
    ]) writeFileSync(join(fixture.artifactsRoot, name), `png:${name}`)
    writeFileSync(join(fixture.artifactsRoot, 'quick-baseline-progress.png'), 'not allowed', 'utf8')
    writeFileSync(join(fixture.artifactsRoot, '171234-failure.png'), 'not allowed', 'utf8')

    stageSafeLifecycleArtifacts({ artifactsRoot: fixture.artifactsRoot, roots: fixture.roots })

    expect(uploadSafeNames(fixture.artifactsRoot)).toEqual([
      'full-candidate-failure.png',
      'full-delete-app-data-failure.png',
      'generation-timeline.json',
      'installer-records-latest-install.json',
      'instrumented-setup.json',
      'lifecycle-report.json',
      'quick-baseline-final.png',
      'quick-delete-all-final.png',
      'quick-preserve-all-final.png',
    ])
    const staged = readFileSync(join(fixture.artifactsRoot, 'upload-safe', 'lifecycle-report.json'), 'utf8')
    expect(staged).toContain(String.raw`$DATA_ROOT\\state.json`)
    expect(staged).not.toContain(fixture.roots.dataRoot)
  })

  it('writes only a minimal failure marker and removes a dangerous JSON output', () => {
    const fixture = createArtifactsFixture()
    writeJson(join(fixture.artifactsRoot, 'lifecycle-report.json'), safeReport('previous-safe', fixture.roots))
    stageSafeLifecycleArtifacts({ artifactsRoot: fixture.artifactsRoot, roots: fixture.roots })
    writeJson(join(fixture.artifactsRoot, 'lifecycle-report.json'), {
      schemaVersion: 1,
      stage: 'raw-secret-stage',
      status: 'Authorization: Bearer sk-secret-token',
      prompt: 'private prompt',
    })

    stageSafeLifecycleArtifacts({ artifactsRoot: fixture.artifactsRoot, roots: fixture.roots })

    const uploadSafe = join(fixture.artifactsRoot, 'upload-safe')
    expect(existsSync(join(uploadSafe, 'lifecycle-report.json'))).toBe(false)
    expect(uploadSafeNames(fixture.artifactsRoot)).toEqual(['redaction-failed.json'])
    const failure = JSON.parse(readFileSync(join(uploadSafe, 'redaction-failed.json'), 'utf8'))
    expect(failure).toEqual({ schemaVersion: 1, stage: 'upload-safe', status: 'redaction-failed' })
    const serialized = JSON.stringify(failure)
    expect(serialized).not.toContain('raw-secret-stage')
    expect(serialized).not.toContain('Authorization')
    expect(serialized).not.toContain('sk-secret-token')
    expect(serialized).not.toContain('private prompt')
  })

  it.each([
    ['API token', 'sk-live-secret-token'],
    ['authorization header', 'Authorization: Bearer secret'],
    ['authorization header without colon', 'Authorization Bearer secret'],
    ['authorization header with case and whitespace variants', 'aUtHoRiZaTiOn \t  BeArEr secret'],
    ['authorization Basic scheme', 'Authorization: Basic Zm9vOmJhcg=='],
    ['authorization assignment', 'Authorization=secret'],
    ['authorization keyword alone', 'Authorization'],
    ['Windows user path', 'C:\\Users\\Mallory\\private.txt'],
    ['conversation body', 'private prompt from a test model'],
    ['session contract prompt', 'SESSION_CONTRACT_PROMPT'],
    ['session contract reply', 'SESSION_CONTRACT_PONG'],
    ['DeepSeek fixture reply', 'E2E_PONG'],
    ['deterministic test-model reply', 'FAKE_MODEL_REPLY'],
    ['first E2E conversation', 'E2E 第一会话 Ω'],
    ['second E2E conversation', 'E2E 第二会话 二'],
  ])('blocks %s that remains in an allowed field', (_caseName, sensitiveText) => {
    const fixture = createArtifactsFixture()
    writeJson(join(fixture.artifactsRoot, 'lifecycle-report.json'), {
      schemaVersion: 1,
      stage: 'scan',
      status: sensitiveText,
    })

    stageSafeLifecycleArtifacts({ artifactsRoot: fixture.artifactsRoot, roots: fixture.roots })

    const uploadSafe = join(fixture.artifactsRoot, 'upload-safe')
    expect(uploadSafeNames(fixture.artifactsRoot)).toEqual(['redaction-failed.json'])
    expect(existsSync(join(uploadSafe, 'lifecycle-report.json'))).toBe(false)
    const failure = JSON.parse(readFileSync(join(uploadSafe, 'redaction-failed.json'), 'utf8'))
    expect(failure).toEqual({ schemaVersion: 1, stage: 'upload-safe', status: 'redaction-failed' })
    expect(JSON.stringify(failure)).not.toContain(sensitiveText)
  })

  it('rebuilds upload-safe so stale files never survive repeated staging', () => {
    const fixture = createArtifactsFixture()
    writeJson(join(fixture.artifactsRoot, 'lifecycle-report.json'), safeReport('first', fixture.roots))
    stageSafeLifecycleArtifacts({ artifactsRoot: fixture.artifactsRoot, roots: fixture.roots })
    writeFileSync(join(fixture.artifactsRoot, 'upload-safe', 'stale-private.log'), 'private', 'utf8')
    rmSync(join(fixture.artifactsRoot, 'lifecycle-report.json'))

    stageSafeLifecycleArtifacts({ artifactsRoot: fixture.artifactsRoot, roots: fixture.roots })

    expect(uploadSafeNames(fixture.artifactsRoot)).toEqual([])
  })

  it('does not follow a reparse-point source outside artifactsRoot', (context) => {
    const fixture = createArtifactsFixture()
    const external = createTemporaryRoot('dsh-external-source-')
    writeJson(join(external, 'latest-install.json'), {
      schemaVersion: 1,
      stage: 'outside',
      status: 'Authorization: Bearer sk-outside-secret',
    })
    if (!tryCreateDirectoryLink(external, join(fixture.artifactsRoot, 'installer-records'))) {
      context.skip('当前 Windows 权限不允许创建测试 junction')
    }

    stageSafeLifecycleArtifacts({ artifactsRoot: fixture.artifactsRoot, roots: fixture.roots })

    expect(uploadSafeNames(fixture.artifactsRoot)).toEqual(['redaction-failed.json'])
    expect(readFileSync(join(external, 'latest-install.json'), 'utf8')).toContain('sk-outside-secret')
  })

  it('refuses to rebuild upload-safe through an external reparse point', (context) => {
    const fixture = createArtifactsFixture()
    const external = createTemporaryRoot('dsh-external-output-')
    const sentinel = join(external, 'must-survive.txt')
    writeFileSync(sentinel, 'outside', 'utf8')
    if (!tryCreateDirectoryLink(external, join(fixture.artifactsRoot, 'upload-safe'))) {
      context.skip('当前 Windows 权限不允许创建测试 junction')
    }

    expect(() => stageSafeLifecycleArtifacts({
      artifactsRoot: fixture.artifactsRoot,
      roots: fixture.roots,
    })).toThrow('不安全路径')
    expect(readFileSync(sentinel, 'utf8')).toBe('outside')
  })
})

describe('recordLifecycleReport', () => {
  it('appends root-tokenized stages, keeps failures, and stages the complete safe timeline', () => {
    const fixture = createArtifactsFixture()
    recordLifecycleReport({
      artifactsRoot: fixture.artifactsRoot,
      roots: fixture.roots,
      category: 'candidate-install',
      stage: 'candidate-install',
      status: 'passed',
      path: join(fixture.roots.dataRoot, 'state', 'provisioning.json'),
    })
    recordLifecycleReport({
      artifactsRoot: fixture.artifactsRoot,
      roots: fixture.roots,
      category: 'state-comparison',
      stage: 'compare',
      status: 'failed',
      differences: ['profile-pending'],
      prompt: 'must-not-be-recorded',
    } as unknown as Parameters<typeof recordLifecycleReport>[0])
    recordLifecycleReport({
      artifactsRoot: fixture.artifactsRoot,
      roots: fixture.roots,
      category: 'cleanup',
      stage: 'cleanup',
      status: 'passed',
    })

    const report = JSON.parse(readFileSync(join(fixture.artifactsRoot, 'lifecycle-report.json'), 'utf8'))
    expect(report).toEqual({
      schemaVersion: 1,
      stages: [
        { category: 'candidate-install', stage: 'candidate-install', status: 'passed', path: '$DATA_ROOT\\state\\provisioning.json' },
        { category: 'state-comparison', stage: 'compare', status: 'failed', differences: ['profile-pending'] },
        { category: 'cleanup', stage: 'cleanup', status: 'passed' },
      ],
    })
    expect(JSON.stringify(report)).not.toContain(fixture.root)
    expect(JSON.stringify(report)).not.toContain('must-not-be-recorded')

    stageSafeLifecycleArtifacts({ artifactsRoot: fixture.artifactsRoot, roots: fixture.roots })
    expect(JSON.parse(readFileSync(join(fixture.artifactsRoot, 'upload-safe', 'lifecycle-report.json'), 'utf8'))).toEqual(report)
  })
})

describe('initializeE2EArtifactsRoot', () => {
  it('does not add ownership to an existing ordinary child or touch its upload-safe directory', () => {
    const root = createTemporaryRoot('dsh-initialize-existing-')
    const e2eRoot = join(root, 'e2e')
    const artifactsRoot = join(e2eRoot, 'existing-artifacts')
    const sentinel = join(artifactsRoot, 'upload-safe', 'must-survive.txt')
    mkdirSync(resolve(sentinel, '..'), { recursive: true })
    writeFileSync(sentinel, 'ordinary', 'utf8')

    expect(() => initializeE2EArtifactsRoot(artifactsRoot, e2eRoot)).toThrow('artifacts-root-not-owned')
    expect(existsSync(join(artifactsRoot, '.dsh-e2e-artifacts-owned'))).toBe(false)
    expect(readFileSync(sentinel, 'utf8')).toBe('ordinary')
  })

  it('initializes a new dedicated child below a verified e2eRoot', () => {
    const root = createTemporaryRoot('dsh-initialize-new-')
    const e2eRoot = join(root, 'e2e')
    const artifactsRoot = join(e2eRoot, 'e2e-artifacts')
    mkdirSync(e2eRoot, { recursive: true })

    expect(initializeE2EArtifactsRoot(artifactsRoot, e2eRoot)).toBe(resolve(artifactsRoot))
    expect(readFileSync(join(artifactsRoot, '.dsh-e2e-artifacts-owned'), 'utf8')).toBe('E2E-owned')
  })

  it('rejects an artifacts root outside e2eRoot before creating it', () => {
    const root = createTemporaryRoot('dsh-initialize-outside-')
    const e2eRoot = join(root, 'e2e')
    const artifactsRoot = join(root, 'outside-artifacts')
    mkdirSync(e2eRoot, { recursive: true })

    expect(() => initializeE2EArtifactsRoot(artifactsRoot, e2eRoot))
      .toThrow('artifacts-root-outside-e2e-root')
    expect(existsSync(artifactsRoot)).toBe(false)
  })

  it('reuses an existing owned artifacts root without rewriting its marker', () => {
    const root = createTemporaryRoot('dsh-initialize-reuse-')
    const e2eRoot = join(root, 'e2e')
    const artifactsRoot = join(e2eRoot, 'e2e-artifacts')
    mkdirSync(e2eRoot, { recursive: true })
    initializeE2EArtifactsRoot(artifactsRoot, e2eRoot)
    const marker = join(artifactsRoot, '.dsh-e2e-artifacts-owned')
    const before = readFileSync(marker, 'utf8')

    expect(initializeE2EArtifactsRoot(artifactsRoot, e2eRoot)).toBe(resolve(artifactsRoot))
    expect(readFileSync(marker, 'utf8')).toBe(before)
  })
})

function createArtifactsFixture() {
  const root = createTemporaryRoot('dsh-lifecycle-report-')
  const e2eRoot = join(root, 'e2e')
  const artifactsRoot = join(e2eRoot, 'artifacts')
  const roots: RedactionRoots = {
    dataRoot: join(root, 'alice', 'data'),
    e2eRoot,
    userHome: join(root, 'alice'),
    temp: join(root, 'temp'),
  }
  mkdirSync(e2eRoot, { recursive: true })
  initializeE2EArtifactsRoot(artifactsRoot, e2eRoot)
  return { root, artifactsRoot, roots }
}

function safeReport(stage: string, roots: RedactionRoots) {
  return {
    schemaVersion: 1,
    stage,
    status: 'passed',
    path: join(roots.dataRoot, 'state.json'),
    apiKey: 'sk-secret-must-be-dropped',
    prompt: 'private prompt must be dropped',
    messages: ['private messages must be dropped'],
    unknownBody: 'private unknown body must be dropped',
  }
}

function writeJson(path: string, value: unknown): void {
  mkdirSync(resolve(path, '..'), { recursive: true })
  writeFileSync(path, JSON.stringify(value, null, 2), 'utf8')
}

function uploadSafeNames(artifactsRoot: string): string[] {
  const uploadSafe = join(artifactsRoot, 'upload-safe')
  if (!existsSync(uploadSafe)) return []
  return readdirSync(uploadSafe, { withFileTypes: true })
    .filter((entry) => entry.isFile())
    .map((entry) => entry.name)
    .sort()
}

function createTemporaryRoot(prefix: string): string {
  const root = mkdtempSync(join(tmpdir(), prefix))
  temporaryRoots.push(root)
  return root
}

function tryCreateDirectoryLink(target: string, path: string): boolean {
  try {
    symlinkSync(target, path, process.platform === 'win32' ? 'junction' : 'dir')
    return true
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'EPERM') return false
    throw error
  }
}
