import { describe, expect, it, vi } from 'vitest'
import { MobileInterruptions, normalizeAnswers, normalizeQuestions } from '../src/mobile-interruptions.ts'

function registry(phoneActive: () => boolean): MobileInterruptions {
  return new MobileInterruptions({
    phoneActive,
    now: () => 1_000,
    log: () => undefined,
    holdMs: 60_000,
    tickMs: 1_000,
  })
}

const approvalRequest = {
  sessionId: 's1',
  toolName: 'bash',
  callId: 'c1',
  reason: 'runs a command',
  questions: null,
}

describe('MobileInterruptions', () => {
  it('settles immediately to delegate when no phone is active', async () => {
    const interruptions = registry(() => false)
    const handle = interruptions.hold(approvalRequest)
    await expect(handle.settled).resolves.toEqual({ kind: 'delegate' })
    expect(interruptions.snapshot()[0]?.delegated).toBe(true)
    expect(interruptions.snapshot()).toHaveLength(1)
    handle.retire()
    expect(interruptions.snapshot()).toHaveLength(0)
  })

  it('holds for an active phone and claims its decision', async () => {
    vi.useFakeTimers()
    try {
      const interruptions = registry(() => true)
      const handle = interruptions.hold(approvalRequest)
      expect(interruptions.decide('missing-key', { action: 'allow' })).toBe('unknown')
      expect(interruptions.decide(handle.record.key, { action: 'nonsense' })).toBeNull()
      expect(interruptions.decide(handle.record.key, { action: 'allow' })).toBe('ok')
      await expect(handle.settled).resolves.toEqual({ kind: 'phone-decision', decision: 'allow' })
      // A second decision on the settled hold is rejected, not double-applied.
      expect(interruptions.decide(handle.record.key, { action: 'reject' })).toBe('unknown')
    } finally {
      vi.useRealTimers()
    }
  })

  it('falls through to the desktop when the phone goes stale', async () => {
    vi.useFakeTimers()
    try {
      let active = true
      const interruptions = registry(() => active)
      const handle = interruptions.hold(approvalRequest)
      active = false
      await vi.advanceTimersByTimeAsync(1_100)
      await expect(handle.settled).resolves.toEqual({ kind: 'delegate' })
      expect(interruptions.snapshot()[0]?.delegated).toBe(true)
    } finally {
      vi.useRealTimers()
    }
  })

  it('aborts a held request when the asker cancels', async () => {
    vi.useFakeTimers()
    try {
      const controller = new AbortController()
      const interruptions = registry(() => true)
      const handle = interruptions.hold(approvalRequest, controller.signal)
      controller.abort()
      await expect(handle.settled).resolves.toEqual({ kind: 'abort' })
    } finally {
      vi.useRealTimers()
    }
  })

  it('validates phone answers against the pending questions', async () => {
    const questions = normalizeQuestions([
      { id: 'q1', question: 'Pick one', options: [{ label: 'A' }, { label: 'B' }] },
      { id: 'q2', question: 'Free text' },
    ])
    expect(questions).not.toBeNull()
    const interruptions = registry(() => true)
    const handle = interruptions.hold({ ...approvalRequest, questions })
    expect(interruptions.decide(handle.record.key, { answers: [{ id: 'q1', selected: ['Z'] }] })).toBeNull()
    expect(interruptions.decide(handle.record.key, { answers: [{ id: 'q1', selected: ['A'] }, { id: 'q2', selected: [], custom: 'typed' }] })).toBe('ok')
    await expect(handle.settled).resolves.toEqual({
      kind: 'phone-answer',
      answers: [{ id: 'q1', selected: ['A'] }, { id: 'q2', selected: [], custom: 'typed' }],
    })
  })
})

describe('normalizeQuestions', () => {
  it('bounds and clones the phone-safe subset', () => {
    const questions = normalizeQuestions([
      { id: 'q1', question: 'Plan?', detail: 'the plan', header: 'Review', multiSelect: true, intent: { kind: 'plan-review', approve: 'Go' }, options: [{ label: 'Go' }, { label: 'No' }] },
    ])
    expect(questions).toEqual([{
      id: 'q1', question: 'Plan?', detail: 'the plan', header: 'Review', multiSelect: true,
      intent: { kind: 'plan-review', approve: 'Go' }, options: [{ label: 'Go' }, { label: 'No' }],
    }])
    expect(normalizeQuestions([])).toBeNull()
    expect(normalizeQuestions('junk')).toBeNull()
    expect(normalizeQuestions([{ id: 'q', question: 'x' }])).toEqual([{ id: 'q', question: 'x' }])
  })
})

describe('normalizeAnswers', () => {
  const questions = [
    { id: 'single', question: 'one', options: [{ label: 'A' }, { label: 'B' }] },
    { id: 'multi', question: 'many', multiSelect: true, options: [{ label: 'X' }, { label: 'Y' }] },
    { id: 'free', question: 'text' },
  ]

  it('accepts single selections, multi selections, and custom text', () => {
    expect(normalizeAnswers([{ id: 'single', selected: ['A'] }], questions)).toEqual([{ id: 'single', selected: ['A'] }])
    expect(normalizeAnswers([{ id: 'multi', selected: ['X', 'Y'] }], questions)).toEqual([{ id: 'multi', selected: ['X', 'Y'] }])
    expect(normalizeAnswers([{ id: 'free', selected: [], custom: 'note' }], questions)).toEqual([{ id: 'free', selected: [], custom: 'note' }])
  })

  it('rejects unknown ids, unknown labels, multi on single-select, and empties', () => {
    expect(normalizeAnswers([{ id: 'nope', selected: ['A'] }], questions)).toBeNull()
    expect(normalizeAnswers([{ id: 'single', selected: ['Z'] }], questions)).toBeNull()
    expect(normalizeAnswers([{ id: 'single', selected: ['A', 'B'] }], questions)).toBeNull()
    expect(normalizeAnswers([{ id: 'free', selected: [] }], questions)).toBeNull()
    expect(normalizeAnswers('junk', questions)).toBeNull()
  })
})
