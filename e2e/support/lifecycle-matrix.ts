import type { UninstallMode } from './installer'

export interface UninstallLifecycleCase {
  mode: UninstallMode
  expected: Record<'app-data' | 'project' | 'external', 'present' | 'absent'>
}

export const UNINSTALL_LIFECYCLE_CASES: readonly UninstallLifecycleCase[] = [
  {
    mode: 'preserve-all',
    expected: { 'app-data': 'present', project: 'present', external: 'present' },
  },
  {
    mode: 'delete-app-data',
    expected: { 'app-data': 'absent', project: 'present', external: 'present' },
  },
  {
    mode: 'delete-all',
    expected: { 'app-data': 'absent', project: 'absent', external: 'present' },
  },
]
