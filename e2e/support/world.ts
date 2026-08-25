import { existsSync, lstatSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { isAbsolute, resolve } from 'node:path'
import { prepareSafeDirectory, assertSafePath } from './safe-path'
import { startAppUpdateFixture, type AppUpdateFixture } from '../../scripts/e2e/app-update-fixture.mjs'
import { startFakeDeepSeek, type FakeDeepSeekFixture } from '../../scripts/e2e/fake-deepseek-server.mjs'
import { startRuntimeFixture, type RuntimeFixture } from '../../scripts/e2e/runtime-fixture-server.mjs'
import { loadRuntimeSigningState } from '../../scripts/e2e/runtime-signing-state.mjs'
import { createFixtureTlsMaterial } from '../../scripts/e2e/tls-fixture.mjs'
import { PackagedDesktopHarness, type DesktopHarness } from './desktop'
import { WindowsInstallerHarness, type InstallerHarness } from './installer'

export interface E2EWorld {
  desktop: DesktopHarness
  installer: InstallerHarness
  runtimeFixture: RuntimeFixture
  modelFixture: FakeDeepSeekFixture
  appUpdateFixture: AppUpdateFixture
  close(): Promise<void>
}

export async function createE2EWorld(): Promise<E2EWorld> {
  const build = loadInstrumentedSetup()
  const e2eRoot = resolve(process.env.DSH_E2E_ROOT ?? '.')
  const projectsRoot = resolve(e2eRoot, 'projects-owned')
  assertSafePath(e2eRoot)
  prepareSafeDirectory(projectsRoot)
  const documentsMarker = resolve(projectsRoot, '.dsh-e2e-documents-owned')
  if (existsSync(documentsMarker) && lstatSync(documentsMarker).isSymbolicLink()) throw new Error('文档所有权标记不得是 symlink')
  writeFileSync(documentsMarker, 'E2E-owned', 'utf8')
  const restoreBuildEnvironment = applyEnvironment({
    DSH_E2E_ROOT: e2eRoot,
    DSH_E2E_DOCUMENTS_ROOT: projectsRoot,
    DSH_E2E_INSTALLER: process.env.DSH_E2E_INSTALLER ?? build.installers.candidate.path,
    DSH_E2E_ARTIFACT_ROOT: process.env.DSH_E2E_ARTIFACT_ROOT ?? build.artifactRoot,
    DSH_E2E_RUNTIME_ARCHIVE: process.env.DSH_E2E_RUNTIME_ARCHIVE ?? build.runtimeArchive,
    DSH_E2E_RUNTIME_SIGNING_STATE: process.env.DSH_E2E_RUNTIME_SIGNING_STATE ?? build.signingState,
    DSH_E2E_RUNTIME_VERSION: process.env.DSH_E2E_RUNTIME_VERSION ?? build.runtimeVersion,
  })
  const tls = createFixtureTlsMaterial()
  const archivePath = requiredFile('DSH_E2E_RUNTIME_ARCHIVE')
  const signingPath = requiredFile('DSH_E2E_RUNTIME_SIGNING_STATE')
  const runtimeFixture = await startRuntimeFixture({
    tls,
    archive: readFileSync(archivePath),
    version: process.env.DSH_E2E_RUNTIME_VERSION ?? '0.1.0-preview',
    signing: loadRuntimeSigningState(signingPath),
    healthPath: '/__desktop/health',
  })
  const modelFixture = await startFakeDeepSeek({ tls })
  const appUpdateFixture = await startAppUpdateFixture({ tls })
  const artifacts = prepareSafeDirectory(resolve(process.env.DSH_E2E_ARTIFACTS ?? 'e2e-artifacts'))
  const caPath = resolve(artifacts, 'loopback-fixture-ca.pem')
  assertSafeLeaf(caPath); writeFileSync(caPath, tls.caCertificate, 'utf8')
  const restoreEnvironment = applyEnvironment({
    DSH_DESKTOP_E2E_RUNTIME_MANIFEST_URL: runtimeFixture.manifestUrl,
    DSH_E2E_RUNTIME_FIXTURE: runtimeFixture.url,
    DSH_E2E_MODEL_ENDPOINT: `${modelFixture.url}/chat/completions`,
    DEEPSEEK_BASE_URL: modelFixture.url,
    DEEPSEEK_API_KEY: 'sk-e2e-desktop-fixture',
    DSH_E2E_APP_UPDATE_ENDPOINT: appUpdateFixture.endpoint,
    NODE_EXTRA_CA_CERTS: caPath,
  })
  const desktop = new PackagedDesktopHarness()
  const installer = new WindowsInstallerHarness({ installers: build.installers })
  let closed = false

  return {
    desktop,
    installer,
    runtimeFixture,
    modelFixture,
    appUpdateFixture,
    async close() {
      if (closed) return
      closed = true
      const desktopResult = await Promise.allSettled([desktop.quit()])
      const results = await Promise.allSettled([
        appUpdateFixture.close(),
        modelFixture.close(),
        runtimeFixture.close(),
      ])
      restoreEnvironment()
      restoreBuildEnvironment()
      const failures = [...desktopResult, ...results].filter(
        (result): result is PromiseRejectedResult => result.status === 'rejected',
      )
      if (failures.length > 0) throw new AggregateError(failures.map((failure) => failure.reason), 'E2E world cleanup failed')
    },
  }
}

interface InstrumentedSetup {
  schemaVersion: 2
  artifactRoot: string
  runtimeArchive: string
  runtimeVersion: string
  signingState: string
  mode: 'quick' | 'full'
  sourceCommit: string | null
  installers: {
    candidate: InstallerArtifact
    baseline?: InstallerArtifact
  }
}

interface InstallerArtifact {
  path: string
  version: string
  sha256: string
}

function loadInstrumentedSetup(): InstrumentedSetup {
  const metadataPath = resolve(process.env.DSH_E2E_ARTIFACTS ?? 'e2e-artifacts', 'instrumented-setup.json')
  if (!existsSync(metadataPath)) {
    throw new Error(`缺少 E2E 构建元数据，请先运行 npm run e2e:setup:build：${metadataPath}`)
  }
  const value = JSON.parse(readFileSync(metadataPath, 'utf8')) as Partial<InstrumentedSetup>
  if (value.schemaVersion !== 2) throw new Error('E2E 构建元数据 schemaVersion 必须是 2')
  for (const key of ['artifactRoot', 'runtimeArchive', 'runtimeVersion', 'signingState'] as const) {
    if (typeof value[key] !== 'string' || value[key]?.trim() === '') {
      throw new Error(`E2E 构建元数据缺少 ${key}`)
    }
  }
  if (value.mode !== 'quick' && value.mode !== 'full') throw new Error('E2E 构建元数据 mode 无效')
  if (value.sourceCommit !== null && typeof value.sourceCommit !== 'string') throw new Error('E2E 构建元数据 sourceCommit 无效')
  if (value.installers === undefined || typeof value.installers !== 'object' || value.installers === null) throw new Error('E2E 构建元数据缺少 installers')
  validateInstaller(value.installers.candidate, 'candidate')
  if (value.installers.baseline !== undefined) validateInstaller(value.installers.baseline, 'baseline')
  return value as InstrumentedSetup
}

function validateInstaller(value: unknown, name: string): asserts value is InstallerArtifact {
  if (typeof value !== 'object' || value === null) throw new Error(`E2E 构建元数据缺少 installers.${name}`)
  const artifact = value as Partial<InstallerArtifact>
  if (typeof artifact.path !== 'string' || !isAbsolute(artifact.path) || artifact.path.trim() === '') throw new Error(`installers.${name}.path 必须是绝对路径`)
  if (typeof artifact.version !== 'string' || artifact.version.trim() === '') throw new Error(`installers.${name}.version 无效`)
  if (typeof artifact.sha256 !== 'string' || !/^[0-9a-fA-F]{64}$/.test(artifact.sha256)) throw new Error(`installers.${name}.sha256 必须是 64 位十六进制`)
}

function requiredFile(name: string): string {
  const value = process.env[name]
  if (value === undefined || !isAbsolute(value) || !existsSync(value)) {
    throw new Error(`${name} 必须指向存在的绝对文件`)
  }
  return value
}

function applyEnvironment(values: Record<string, string>): () => void {
  const previous = new Map<string, string | undefined>()
  for (const [name, value] of Object.entries(values)) {
    previous.set(name, process.env[name])
    process.env[name] = value
  }
  return () => {
    for (const [name, value] of previous) {
      if (value === undefined) delete process.env[name]
      else process.env[name] = value
    }
  }
}
