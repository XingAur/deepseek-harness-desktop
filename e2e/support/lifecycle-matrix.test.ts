import { describe, expect, it } from 'vitest'
import { UNINSTALL_LIFECYCLE_CASES } from './lifecycle-matrix'

describe('UNINSTALL_LIFECYCLE_CASES', () => {
  it('covers every explicit uninstall choice with the required sentinel outcomes', () => {
    expect(UNINSTALL_LIFECYCLE_CASES).toEqual([
      { mode: 'preserve-all', expected: { 'app-data': 'present', project: 'present', external: 'present' } },
      { mode: 'delete-app-data', expected: { 'app-data': 'absent', project: 'present', external: 'present' } },
      { mode: 'delete-all', expected: { 'app-data': 'absent', project: 'absent', external: 'present' } },
    ])
  })
})
