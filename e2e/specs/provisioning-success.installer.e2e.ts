import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { createE2EWorld, type E2EWorld } from '../support/world'

let world: E2EWorld

beforeAll(async () => {
  world = await createE2EWorld()
})

afterAll(async () => {
  await world?.close()
})

describe('Windows Web Setup success path', () => {
  it('installs a ready runtime and starts twice without Runtime update traffic', async () => {
    const { desktop, installer, runtimeFixture } = world
    const installation = await installer.installClean()

    expect(installation.exitCode).toBe(0)
    expect(installation.receipt?.runtimeVersion).toBe(runtimeFixture.version)
    expect(runtimeFixture.requests().filter((request) => request.path === '/runtime.zip')).toHaveLength(1)
    expect(installation.appBinary).toBeTypeOf('string')

    runtimeFixture.clearRequests()
    await desktop.launch(installation.appBinary)
    await desktop.waitForWorkbench(30_000)
    const firstTiming = latestActiveTiming(installation.dataRoot)
    expect(runtimeFixture.requests().filter((request) => request.path === '/runtime.zip')).toHaveLength(0)
    expectRequestsAfterActive(runtimeFixture.requests(), firstTiming.activeAt)
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
  })

  it('creates a unicode project and receives the deterministic model reply', async () => {
    const { desktop, modelFixture } = world
    const projectPath = desktop.fixturePath('e2e-artifacts/测试 项目 Ω')

    await desktop.createProject({
      idea: '请创建 README，并在完成后回复确认',
      path: projectPath,
      permission: 'workspace-write',
    })

    await desktop.waitForWorkbenchText('E2E_PONG')
    expect(modelFixture.requests()).toEqual(expect.arrayContaining([
      expect.objectContaining({ method: 'POST', path: '/chat/completions' }),
    ]))
  })
})

interface TimelineEntry {
  generationId: string
  phase: string
  recordedAt: string
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
