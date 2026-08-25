import { describe, expect, it } from 'vitest'
import { selectInstallerPath } from '../../e2e/support/installer'

describe('installer selector', () => {
  const explicit = 'C:/explicit.exe'
  const installers = {
    candidate: { path: 'C:/candidate.exe', version: '1', sha256: 'a'.repeat(64) },
    baseline: { path: 'C:/baseline.exe', version: '1', sha256: 'b'.repeat(64) },
  }
  it('keeps explicit installer when variant is omitted', () => expect(selectInstallerPath(explicit, installers)).toBe(explicit))
  it('selects metadata candidate only when requested', () => expect(selectInstallerPath(explicit, installers, 'candidate')).toBe('C:/candidate.exe'))
  it('rejects missing baseline metadata', () => expect(() => selectInstallerPath(explicit, { candidate: installers.candidate }, 'baseline')).toThrow(/baseline/))
})
