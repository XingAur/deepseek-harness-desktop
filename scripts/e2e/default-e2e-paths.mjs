import { join, resolve } from 'node:path'

export const DEFAULT_E2E_ROOT_DIRECTORY = '.dsh-e2e-owned'

export function resolveE2EPaths(cwd = process.cwd(), env = process.env) {
  const e2eRoot = resolve(cwd, env.DSH_E2E_ROOT ?? DEFAULT_E2E_ROOT_DIRECTORY)
  const artifactsRoot = resolve(cwd, env.DSH_E2E_ARTIFACTS ?? join(e2eRoot, 'e2e-artifacts'))
  return { e2eRoot, artifactsRoot, usesDefaultRoot: env.DSH_E2E_ROOT === undefined }
}

export function withE2EPaths(env, paths) {
  return {
    ...env,
    DSH_E2E_ROOT: paths.e2eRoot,
    DSH_E2E_ARTIFACTS: paths.artifactsRoot,
  }
}
