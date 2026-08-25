import { describe, expect, it } from 'vitest'
import { E2E_RUNTIME_IDENTITY_COMMAND, runtimePidFromE2eIdentity } from './e2e-runtime-identity'

describe('e2e runtime identity bridge', () => {
  it('只接受 Rust 活动 Runtime 返回的正安全整数 PID', () => {
    expect(E2E_RUNTIME_IDENTITY_COMMAND).toBe('e2e_runtime_identity')
    expect(runtimePidFromE2eIdentity({ runtimePid: 42_001 })).toBe(42_001)
    expect(runtimePidFromE2eIdentity({ runtimePid: 0 })).toBeNull()
    expect(runtimePidFromE2eIdentity({ runtimePid: -1 })).toBeNull()
    expect(runtimePidFromE2eIdentity({ runtimePid: 1.5 })).toBeNull()
    expect(runtimePidFromE2eIdentity({ runtimePid: '42001' })).toBeNull()
    expect(runtimePidFromE2eIdentity(null)).toBeNull()
  })
})
