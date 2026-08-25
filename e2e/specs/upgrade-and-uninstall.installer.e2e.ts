import { resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { expectNoRecordedProcessOrPort, expectSentinelScopes } from '../support/assertions'
import { closeCleanupAndStage } from '../support/lifecycle-cleanup'
import type { InstallationRecord } from '../support/installer'
import { UNINSTALL_LIFECYCLE_CASES, type UninstallLifecycleCase } from '../support/lifecycle-matrix'
import {
  lifecycleRedactionRoots,
  recordLifecycleReport,
  stageSafeLifecycleArtifacts,
  type RedactionRoots,
} from '../support/lifecycle-report'
import { captureLifecycleSnapshot, captureProjectPath, compareUpgradeState } from '../support/lifecycle-state'
import { createE2EWorld, type E2EWorld } from '../support/world'

const FIRST_SESSION_MARKER = 'E2E 第一会话 Ω'
const SECOND_SESSION_MARKER = 'E2E 第二会话 二'
const CONTINUATION_PROMPT = 'E2E 升级后继续 Ω'
let world: E2EWorld | undefined
let latestDataRoot: string | undefined
let lifecycleContext: LifecycleContext | undefined

interface LifecycleContext {
  artifactsRoot: string
  roots: RedactionRoots
}

describe.runIf(process.env.DSH_E2E_MODE === 'full')('Windows Web Setup upgrade and uninstall lifecycle', () => {
  beforeEach(async () => {
    world = await createE2EWorld()
    lifecycleContext = captureLifecycleContext()
  })

  afterEach(async () => {
    const completedWorld = world
    const completedContext = lifecycleContext
    world = undefined
    lifecycleContext = undefined
    latestDataRoot = undefined
    if (completedWorld === undefined || completedContext === undefined) return
    await closeCleanupAndStage({
      close: () => completedWorld.close(),
      cleanup: () => completedWorld.installer.cleanupRecordedProcesses(),
      stage: () => stageSafeLifecycleArtifacts(completedContext),
    })
  })

  it('upgrades baseline state to candidate without losing sessions or project state', async () => {
    const current = requireWorld()
    const { desktop, installer } = current
    const baseline = await lifecycleStage('baseline-install', 'baseline-install', async () => {
      const installation = await installer.installClean('baseline')
      setLatestDataRoot(installation.dataRoot)
      return installation
    })
    const baselineBinary = requireAppBinary(baseline)

    await desktop.launch(baselineBinary)
    await desktop.waitForWorkbench(120_000)
    await createTwoSessionState(current)
    const projectPath = captureProjectPath(baseline.dataRoot)
    await installer.writePreservationSentinels(projectPath)
    const baselineSnapshot = captureLifecycleSnapshot({
      dataRoot: baseline.dataRoot,
      projectPath,
      roots: lifecycleRedactionRoots(baseline.dataRoot),
    })
    await desktop.quit()

    const candidate = await lifecycleStage('candidate-install', 'install-over-baseline', async () => {
      const installation = await installer.installOver('candidate')
      setLatestDataRoot(installation.dataRoot)
      return installation
    })
    const candidateBinary = requireAppBinary(candidate)
    await desktop.launch(candidateBinary)
    await desktop.waitForWorkbench(8_000)
    const candidateSnapshot = captureLifecycleSnapshot({
      dataRoot: candidate.dataRoot,
      projectPath,
      roots: lifecycleRedactionRoots(candidate.dataRoot),
    })
    await lifecycleStage('state-comparison', 'baseline-to-candidate', async () => {
      const differences = compareUpgradeState(baselineSnapshot, candidateSnapshot)
      expect(differences).toEqual([])
      return { snapshot: candidateSnapshot, differences }
    })
    await desktop.assertSessionRoundTrip([FIRST_SESSION_MARKER, SECOND_SESSION_MARKER])

    const requestsBeforeContinuation = modelRequestCount(current)
    await desktop.continueConversation(CONTINUATION_PROMPT)
    expectNewModelRequest(current, requestsBeforeContinuation, CONTINUATION_PROMPT)
    await desktop.assertSessionRoundTrip([FIRST_SESSION_MARKER, SECOND_SESSION_MARKER, CONTINUATION_PROMPT])
    const stoppedCandidateIdentity = await installer.recordRuntimeIdentity({
      runtimePid: await desktop.runtimePid(),
      runtimePort: await desktop.runtimePort(),
    })
    await desktop.quit()
    await expectNoRecordedProcessOrPort(stoppedCandidateIdentity)

    await desktop.launch(candidateBinary)
    await desktop.waitForWorkbench(8_000)
    await desktop.assertSessionRoundTrip([FIRST_SESSION_MARKER, SECOND_SESSION_MARKER, CONTINUATION_PROMPT])
    await installer.recordRuntimeIdentity({
      runtimePid: await desktop.runtimePid(),
      runtimePort: await desktop.runtimePort(),
    })
  })

  it.each(UNINSTALL_LIFECYCLE_CASES)('uninstalls $mode with the expected preservation scopes', async (scenario) => {
    await runUninstallScenario(requireWorld(), scenario)
  })
})

async function createTwoSessionState(world: E2EWorld): Promise<void> {
  await world.desktop.createProject({ idea: `${FIRST_SESSION_MARKER}：请创建 README，并在完成后回复确认` })
  await world.desktop.createConversation(`${SECOND_SESSION_MARKER}：请回复确认`)
  await world.desktop.assertSessionRoundTrip([FIRST_SESSION_MARKER, SECOND_SESSION_MARKER])
}

async function runUninstallScenario(world: E2EWorld, scenario: UninstallLifecycleCase): Promise<void> {
  const { desktop, installer } = world
  const installation = await lifecycleStage('candidate-install', `uninstall-${scenario.mode}-install`, async () => {
    const installed = await installer.installClean('candidate')
    setLatestDataRoot(installed.dataRoot)
    return installed
  })
  await desktop.launch(requireAppBinary(installation))
  await desktop.waitForWorkbench(120_000)
  await desktop.createProject({ idea: `E2E 卸载 ${scenario.mode} Ω` })
  const projectPath = captureProjectPath(installation.dataRoot)
  const sentinels = await installer.writePreservationSentinels(projectPath)
  const identity = await installer.recordRuntimeIdentity({
    runtimePid: await desktop.runtimePid(),
    runtimePort: await desktop.runtimePort(),
  })
  await desktop.quit()

  await lifecycleStage(`uninstall-${scenario.mode}`, 'uninstall', async () => {
    await installer.uninstall(scenario.mode)
    expect(await installer.appBinaryExists()).toBe(false)
    await expectNoRecordedProcessOrPort(identity)
    await expectSentinelScopes(sentinels, scenario.expected)
  })
  if (scenario.expected.project === 'present') installer.cleanupOwnedProject(projectPath)
}

function requireWorld(): E2EWorld {
  if (world === undefined) throw new Error('E2E world 未初始化')
  return world
}

async function lifecycleStage<T>(category: string, stage: string, action: () => Promise<T>): Promise<T> {
  try {
    const result = await action()
    writeStageReport(category, stage, 'passed', reportDetails(result))
    return result
  } catch (error) {
    try {
      writeStageReport(category, stage, 'failed')
    } catch (reportError) {
      throw new AggregateError([error, reportError], `${stage} 与失败报告均失败`)
    }
    throw error
  }
}

function writeStageReport(
  category: string,
  stage: string,
  status: 'passed' | 'failed',
  details: Pick<Parameters<typeof recordLifecycleReport>[0], 'snapshot' | 'differences'> = {},
): void {
  if (lifecycleContext === undefined) return
  recordLifecycleReport({
    ...lifecycleContext,
    category,
    stage,
    status,
    ...details,
  })
}

function setLatestDataRoot(dataRoot: string): void {
  latestDataRoot = dataRoot
  lifecycleContext = captureLifecycleContext(dataRoot)
}

function captureLifecycleContext(dataRoot?: string): LifecycleContext {
  const e2eRoot = resolve(process.env.DSH_E2E_ROOT ?? '.dsh-e2e-owned')
  const artifactsRoot = resolve(process.env.DSH_E2E_ARTIFACTS ?? resolve(e2eRoot, 'e2e-artifacts'))
  return {
    artifactsRoot,
    roots: lifecycleRedactionRoots(dataRoot ?? resolve(e2eRoot, 'lifecycle-failure-placeholder')),
  }
}

function reportDetails(value: unknown): Pick<Parameters<typeof recordLifecycleReport>[0], 'snapshot' | 'differences'> {
  if (Array.isArray(value)) return { differences: value }
  if (typeof value === 'object' && value !== null && 'snapshot' in value && 'differences' in value) {
    return value as Pick<Parameters<typeof recordLifecycleReport>[0], 'snapshot' | 'differences'>
  }
  return {}
}

function requireAppBinary(installation: InstallationRecord): string {
  if (installation.appBinary === undefined) throw new Error('安装记录缺少应用路径')
  return installation.appBinary
}

function modelRequestCount(world: E2EWorld): number {
  return world.modelFixture.requests().filter((request) => request.method === 'POST' && request.path === '/chat/completions').length
}

function expectNewModelRequest(world: E2EWorld, before: number, prompt: string): void {
  const requests = world.modelFixture.requests()
    .filter((request) => request.method === 'POST' && request.path === '/chat/completions')
    .slice(before)
  expect(requests.length).toBeGreaterThan(0)
  expect(requests.some((request) => request.body.includes(prompt))).toBe(true)
}
