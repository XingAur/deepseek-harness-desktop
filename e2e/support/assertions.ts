import { createHash } from 'node:crypto'
import { existsSync, readFileSync } from 'node:fs'
import { Socket } from 'node:net'
import { expect } from 'vitest'
import type { InstallationRecord, PreservationSentinels } from './installer'

export async function expectNoRecordedProcessOrPort(record: InstallationRecord): Promise<void> {
  for (const pid of [record.desktopPid, record.runtimePid]) {
    if (pid === undefined) continue
    expect(isProcessAlive(pid), `recorded PID ${pid} should have exited`).toBe(false)
  }
  if (record.runtimePort !== undefined) {
    expect(await canConnect(record.runtimePort), `recorded port ${record.runtimePort} should be closed`).toBe(false)
  }
}

export async function expectPreserved(sentinels: PreservationSentinels): Promise<void> {
  for (const sentinel of sentinels.entries) {
    expect(existsSync(sentinel.path), `${sentinel.path} should be preserved`).toBe(true)
    expect(sha256(readFileSync(sentinel.path))).toBe(sentinel.sha256)
  }
}

export async function expectSentinelScopes(
  sentinels: PreservationSentinels,
  expected: Record<'app-data' | 'project' | 'external', 'present' | 'absent'>,
): Promise<void> {
  const scopes = new Set(sentinels.entries.map((entry) => entry.scope))
  for (const scope of ['app-data', 'project', 'external'] as const) {
    expect(scopes.has(scope), `${scope} sentinel should exist`).toBe(true)
  }
  for (const sentinel of sentinels.entries) {
    const present = existsSync(sentinel.path)
    expect(present, `${sentinel.path} presence`).toBe(expected[sentinel.scope] === 'present')
    if (present) expect(sha256(readFileSync(sentinel.path))).toBe(sentinel.sha256)
  }
}

function isProcessAlive(pid: number): boolean {
  try {
    process.kill(pid, 0)
    return true
  } catch {
    return false
  }
}

function canConnect(port: number) {
  return new Promise<boolean>((resolveConnection) => {
    const socket = new Socket()
    socket.setTimeout(500)
    socket.once('connect', () => {
      socket.destroy()
      resolveConnection(true)
    })
    socket.once('timeout', () => {
      socket.destroy()
      resolveConnection(false)
    })
    socket.once('error', () => resolveConnection(false))
    socket.connect(port, '127.0.0.1')
  })
}

function sha256(value: Uint8Array) {
  return createHash('sha256').update(value).digest('hex')
}
