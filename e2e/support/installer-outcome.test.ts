import { describe, expect, it } from 'vitest'
import { assertWebSetupOutcome } from './installer'

describe('Web Setup command outcome', () => {
  it('does not treat the recorded successful installer exit as a PowerShell failure', () => {
    expect(() => assertWebSetupOutcome(false, false, 0)).not.toThrow()
  })

  it('reports a post-install PowerShell validation failure separately from normal installer stderr', () => {
    expect(() => assertWebSetupOutcome(false, true, 0))
      .toThrow('Web Setup PowerShell validation failed after installer exit code 0')
  })
})
