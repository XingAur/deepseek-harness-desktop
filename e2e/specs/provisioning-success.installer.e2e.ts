import { readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { expectNoRecordedProcessOrPort, expectSentinelScopes } from '../support/assertions'
import { lifecycleRedactionRoots, stageSafeLifecycleArtifacts } from '../support/lifecycle-report'
import { createE2EWorld, type E2EWorld } from '../support/world'

let world: E2EWorld
let latestDataRoot: string | undefined

const FIRST_SESSION_MARKER = 'E2E 第一会话 Ω'
const SECOND_SESSION_MARKER = 'E2E 第二会话 二'
const CONTINUATION_PROMPT = 'E2E 升级后继续 Ω'

beforeAll(async () => {
  world = await createE2EWorld()
})

// 场景独立安装时，即使前一条断言失败也必须释放桌面和 Runtime，
// 否则下一条 reset 会因 Windows 占用 runtime/node.exe 而失败。
afterEach(async () => {
  await world?.desktop.quit()
})

afterAll(async () => {
  const failures: unknown[] = []
  try {
    if (latestDataRoot !== undefined) {
      stageSafeLifecycleArtifacts({
        artifactsRoot: resolve(process.env.DSH_E2E_ARTIFACTS ?? 'e2e-artifacts'),
        roots: lifecycleRedactionRoots(latestDataRoot),
      })
    }
  } catch (error) {
    failures.push(error)
  }
  try {
    await world?.close()
  } catch (error) {
    failures.push(error)
  }
  if (failures.length === 1) throw failures[0]
  if (failures.length > 1) throw new AggregateError(failures, 'E2E 生命周期收尾失败')
})

describe('Windows Web Setup success path', () => {
  it('installs the desktop shell, provisions Runtime on first start, and starts warm without update traffic', async () => {
    const { desktop, installer, runtimeFixture } = world
    const installation = await installer.installClean()

    expect(installation.exitCode).toBe(0)
    expect(installation.receipt).toBeNull()
    expect(runtimeFixture.requests().filter((request) => request.path === '/runtime.zip')).toHaveLength(0)
    expect(installation.appBinary).toBeTypeOf('string')
    if (installation.appBinary === undefined) throw new Error('安装记录缺少应用路径')
    latestDataRoot = installation.dataRoot

    runtimeFixture.clearRequests()
    await desktop.launch(installation.appBinary)
    // 首次安装需要完成 Runtime 下载、校验、解压与健康检查；GitHub Windows runner
    // 在冷缓存下可能超过 120 秒，不能把仍在推进的首启误判为失败。
    await desktop.waitForWorkbench(180_000)
    const firstTiming = latestActiveTiming(installation.dataRoot)
    expect(runtimeFixture.requests().filter((request) => request.path === '/runtime.zip')).toHaveLength(1)
    expect(readProvisioningReceipt(installation.dataRoot).runtimeVersion).toBe(runtimeFixture.version)
    await desktop.quit()

    runtimeFixture.clearRequests()
    await desktop.launch(installation.appBinary)
    await desktop.waitForWorkbench(8_000)
    const warmTiming = latestActiveTiming(installation.dataRoot)
    expect(warmTiming.elapsedMs).toBeLessThanOrEqual(8_000)
    expect(runtimeFixture.requests().filter((request) => request.path === '/runtime.zip')).toHaveLength(0)
    expectRequestsAfterActive(runtimeFixture.requests(), warmTiming.activeAt)

    process.stdout.write(JSON.stringify({
      firstGenerationElapsedMs: firstTiming.elapsedMs,
      warmGenerationElapsedMs: warmTiming.elapsedMs,
    }) + '\n')
    await desktop.quit()
  })

  it('creates two sessions, switches without refresh, and restores them after restart', async () => {
    const { desktop, installer, modelFixture } = world
    // 这条用例必须从空工作台开始。项目构建完成后会进入该项目的专用会话，
    // 不再展示全局“新建会话”入口；复用上一条的状态会把 UI 差异误判为会话回归。
    const installation = await installer.installClean()
    if (installation.appBinary === undefined) throw new Error('安装记录缺少应用路径')
    latestDataRoot = installation.dataRoot

    await desktop.launch(installation.appBinary)
    await desktop.waitForWorkbench(180_000)
    await desktop.createConversation(`${FIRST_SESSION_MARKER}：请回复确认`)
    await desktop.createConversation(`${SECOND_SESSION_MARKER}：请回复确认`)
    await desktop.assertSessionRoundTrip([FIRST_SESSION_MARKER, SECOND_SESSION_MARKER])
    expect(modelFixture.requests()).toEqual(expect.arrayContaining([
      expect.objectContaining({ method: 'POST', path: '/chat/completions' }),
    ]))

    await desktop.quit()
    await desktop.launch(installation.appBinary)
    await desktop.waitForWorkbench(8_000)
    const requestCountBeforeContinuation = modelFixture.requests().filter(
      (request) => request.method === 'POST' && request.path === '/chat/completions',
    ).length
    await desktop.continueConversation(CONTINUATION_PROMPT)
    const continuationRequests = modelFixture.requests().filter(
      (request) => request.method === 'POST' && request.path === '/chat/completions',
    ).slice(requestCountBeforeContinuation)
    expect(continuationRequests.length).toBeGreaterThan(0)
    expect(continuationRequests.some((request) => request.body.includes(CONTINUATION_PROMPT))).toBe(true)
    await desktop.assertSessionRoundTrip([FIRST_SESSION_MARKER, SECOND_SESSION_MARKER, CONTINUATION_PROMPT])
    const recordedRuntimeIdentity = await installer.recordRuntimeIdentity({
      runtimePid: await desktop.runtimePid(),
      runtimePort: await desktop.runtimePort(),
    })
    expect(recordedRuntimeIdentity.runtimePid).toBeGreaterThan(0)
    await desktop.quit()
  })

  it('默认卸载仅移除应用，并保留应用数据、项目与外部哨兵', async () => {
    const { desktop, installer } = world
    const installation = await installer.installClean()
    if (installation.appBinary === undefined) throw new Error('安装记录缺少应用路径')
    latestDataRoot = installation.dataRoot
    await desktop.launch(installation.appBinary)
    await desktop.waitForWorkbench(180_000)
    const sentinels = await installer.writePreservationSentinels()
    const recordedRuntimeIdentity = await installer.recordRuntimeIdentity({
      runtimePid: await desktop.runtimePid(),
      runtimePort: await desktop.runtimePort(),
    })

    await desktop.quit()
    await installer.uninstall('preserve-all')
    expect(await installer.appBinaryExists()).toBe(false)
    if (recordedRuntimeIdentity === undefined) throw new Error('安装记录缺少 Runtime 身份')
    await expectNoRecordedProcessOrPort(recordedRuntimeIdentity)
    await expectSentinelScopes(sentinels, {
      'app-data': 'present',
      project: 'present',
      external: 'present',
    })
  })
})

interface TimelineEntry {
  generationId: string
  phase: string
  recordedAt: string
}

function readProvisioningReceipt(dataRoot: string): { runtimeVersion: string } {
  return JSON.parse(readFileSync(join(dataRoot, 'state', 'provisioning.json'), 'utf8')) as { runtimeVersion: string }
}

function latestActiveTiming(dataRoot: string) {
  const entries = JSON.parse(readFileSync(join(dataRoot, 'generation-timeline.json'), 'utf8')) as TimelineEntry[]
  const active = entries.slice().reverse().find((entry) => entry.phase === 'active')
  if (active === undefined) throw new Error('Generation timeline 缺少 active 事件')
  const start = entries.find((entry) => entry.generationId === active.generationId && entry.phase === 'resolving-profile')
  if (start === undefined) throw new Error('Generation timeline 缺少 resolving-profile 事件')
  return {
    activeAt: Date.parse(active.recordedAt),
    elapsedMs: Date.parse(active.recordedAt) - Date.parse(start.recordedAt),
  }
}

function expectRequestsAfterActive(requests: readonly { at?: string }[], activeAt: number) {
  for (const request of requests) {
    expect(Date.parse(request.at ?? '')).toBeGreaterThanOrEqual(activeAt)
  }
}
