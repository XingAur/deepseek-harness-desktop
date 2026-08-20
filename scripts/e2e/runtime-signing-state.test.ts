import { mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { createRuntimeSigningState, loadRuntimeSigningState } from './runtime-signing-state.mjs'

describe('runtime signing state', () => {
  it('persists the key used by both the E2E build and fixture', () => {
    const path = join(mkdtempSync(join(tmpdir(), 'dsh-runtime-signing-')), 'state.json')
    const created = createRuntimeSigningState(path)
    const loaded = loadRuntimeSigningState(path)

    expect(loaded.publicKey).toBe(created.publicKey)
    expect(loaded.privateKey.type).toBe('private')
  })
})
