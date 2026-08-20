import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { isAbsolute, resolve } from 'node:path'
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
  const restoreBuildEnvironment = applyEnvironment({
    DSH_E2E_ROOT: process.env.DSH_E2E_ROOT ?? resolve('.'),
    DSH_E2E_INSTALLER: process.env.DSH_E2E_INSTALLER ?? build.installer,
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
  const artifacts = resolve(process.env.DSH_E2E_ARTIFACTS ?? 'e2e-artifacts')
  mkdirSync(artifacts, { recursive: true })
  const caPath = resolve(artifacts, 'loopback-fixture-ca.pem')
  writeFileSync(caPath, tls.caCertificate, 'utf8')
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
  const installer = new WindowsInstallerHarness()
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
  installer: string
  artifactRoot: string
  runtimeArchive: string
  runtimeVersion: string
  signingState: string
}

function loadInstrumentedSetup(): InstrumentedSetup {
  const metadataPath = resolve(process.env.DSH_E2E_ARTIFACTS ?? 'e2e-artifacts', 'instrumented-setup.json')
  if (!existsSync(metadataPath)) {
    throw new Error(`缺少 E2E 构建元数据，请先运行 npm run e2e:setup:build：${metadataPath}`)
  }
  const value = JSON.parse(readFileSync(metadataPath, 'utf8')) as Partial<InstrumentedSetup>
  for (const key of ['installer', 'artifactRoot', 'runtimeArchive', 'runtimeVersion', 'signingState'] as const) {
    if (typeof value[key] !== 'string' || value[key]?.trim() === '') {
      throw new Error(`E2E 构建元数据缺少 ${key}`)
    }
  }
  return value as InstrumentedSetup
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
