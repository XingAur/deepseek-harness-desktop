import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { createE2EWorld, type E2EWorld } from '../support/world'

let world: E2EWorld
let appBinary: string

const FIRST_SESSION_MARKER = 'E2E 第一会话 Ω'
const SECOND_SESSION_MARKER = 'E2E 第二会话 二'

beforeAll(async () => {
  world = await createE2EWorld()
})

afterAll(async () => {
  await world?.close()
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
    appBinary = installation.appBinary

    runtimeFixture.clearRequests()
    await desktop.launch(appBinary)
    await desktop.waitForWorkbench(30_000)
    const firstTiming = latestActiveTiming(installation.dataRoot)
    expect(runtimeFixture.requests().filter((request) => request.path === '/runtime.zip')).toHaveLength(1)
    expect(readProvisioningReceipt(installation.dataRoot).runtimeVersion).toBe(runtimeFixture.version)
    await desktop.quit()

    runtimeFixture.clearRequests()
    await desktop.launch(appBinary)
    await desktop.waitForWorkbench(8_000)
    const warmTiming = latestActiveTiming(installation.dataRoot)
    expect(warmTiming.elapsedMs).toBeLessThanOrEqual(8_000)
    expect(runtimeFixture.requests().filter((request) => request.path === '/runtime.zip')).toHaveLength(0)
    expectRequestsAfterActive(runtimeFixture.requests(), warmTiming.activeAt)

    process.stdout.write(JSON.stringify({
      firstGenerationElapsedMs: firstTiming.elapsedMs,
      warmGenerationElapsedMs: warmTiming.elapsedMs,
    }) + '\n')
  })

  it('creates two sessions, switches without refresh, and restores them after restart', async () => {
    const { desktop, modelFixture } = world

    await desktop.createProject({
      idea: `${FIRST_SESSION_MARKER}：请创建 README，并在完成后回复确认`,
    })
    await desktop.createConversation(`${SECOND_SESSION_MARKER}：请回复确认`)
    await desktop.assertSessionRoundTrip([FIRST_SESSION_MARKER, SECOND_SESSION_MARKER])
    expect(modelFixture.requests()).toEqual(expect.arrayContaining([
      expect.objectContaining({ method: 'POST', path: '/chat/completions' }),
    ]))

    await desktop.quit()
    await desktop.launch(appBinary)
    await desktop.waitForWorkbench(8_000)
    await desktop.assertSessionRoundTrip([FIRST_SESSION_MARKER, SECOND_SESSION_MARKER])
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
