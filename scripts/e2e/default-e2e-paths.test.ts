import { join, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { DEFAULT_E2E_ROOT_DIRECTORY, resolveE2EPaths, withE2EPaths } from './default-e2e-paths.mjs'

describe('default E2E paths', () => {
  it('uses a new dedicated root rather than the historical cwd/e2e-artifacts directory', () => {
    const paths = resolveE2EPaths('E:/repo', {})
    expect(paths).toEqual({
      e2eRoot: resolve('E:/repo', DEFAULT_E2E_ROOT_DIRECTORY),
      artifactsRoot: resolve('E:/repo', DEFAULT_E2E_ROOT_DIRECTORY, 'e2e-artifacts'),
      usesDefaultRoot: true,
    })
    expect(paths.artifactsRoot).not.toBe(resolve('E:/repo', 'e2e-artifacts'))
  })

  it('keeps explicit roots and artifacts together in child process environment', () => {
    const paths = resolveE2EPaths('E:/repo', { DSH_E2E_ROOT: 'E:/controlled', DSH_E2E_ARTIFACTS: 'E:/controlled/artifacts' })
    expect(paths).toEqual({ e2eRoot: resolve('E:/controlled'), artifactsRoot: resolve('E:/controlled/artifacts'), usesDefaultRoot: false })
    expect(withE2EPaths({ KEEP: '1' }, paths)).toEqual({
      KEEP: '1',
      DSH_E2E_ROOT: resolve('E:/controlled'),
      DSH_E2E_ARTIFACTS: resolve('E:/controlled/artifacts'),
    })
  })
})
