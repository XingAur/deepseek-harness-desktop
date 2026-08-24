import { describe, expect, it } from 'vitest'
import { OperationLedger, reconcileTask, type RecoveryInput } from './recovery.js'

const base = (overrides: Partial<RecoveryInput> = {}): RecoveryInput => ({
  storedStatus: 'running',
  workerAlive: false,
  workerIdentityKnownDead: true,
  pendingApproval: false,
  lastAcknowledgedSequence: 4,
  observedSequence: 4,
  externalResult: 'none',
  ...overrides,
})

describe('agent recovery state machine', () => {
  it('keeps completed and cancelled tasks terminal and marks normal interruption recoverable', () => {
    expect(reconcileTask(base({ storedStatus: 'completed' })).status).toBe('completed')
    expect(reconcileTask(base({ storedStatus: 'cancelled' })).status).toBe('cancelled')
    expect(reconcileTask(base()).status).toBe('recoverable')
  })

  it('does not relaunch a worker until its old process is known dead', () => {
    const result = reconcileTask(base({ workerAlive: true, workerIdentityKnownDead: false }))
    expect(result).toMatchObject({ status: 'running', canStartReplacement: false })
    expect(reconcileTask(base({ workerAlive: false, workerIdentityKnownDead: false })).canStartReplacement).toBe(false)
  })

  it('keeps pending approvals waiting and turns event gaps into reviewable recovery', () => {
    expect(reconcileTask(base({ pendingApproval: true })).status).toBe('waiting-approval')
    expect(reconcileTask(base({ observedSequence: 7 })).status).toBe('needs-review')
    expect(reconcileTask(base({ externalResult: 'unknown' })).status).toBe('needs-review')
  })

  it('never replays an operation whose external result is unknown', () => {
    expect(reconcileTask(base({ externalResult: 'unknown' })).replayExternalOperation).toBe(false)
    expect(reconcileTask(base({ externalResult: 'confirmed' })).replayExternalOperation).toBe(false)
  })

  it('deduplicates idempotency keys and acknowledges only once', () => {
    const ledger = new OperationLedger()
    expect(ledger.begin('task-a:write-1')).toBe('new')
    expect(ledger.begin('task-a:write-1')).toBe('duplicate')
    expect(ledger.acknowledge('task-a:write-1')).toBe(true)
    expect(ledger.acknowledge('task-a:write-1')).toBe(false)
    expect(ledger.begin('task-a:write-1')).toBe('duplicate')
  })
})
