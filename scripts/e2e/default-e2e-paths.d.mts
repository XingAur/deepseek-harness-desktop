export const DEFAULT_E2E_ROOT_DIRECTORY: string

export interface E2EPaths {
  e2eRoot: string
  artifactsRoot: string
  usesDefaultRoot: boolean
}

export function resolveE2EPaths(cwd?: string, env?: Record<string, string | undefined>): E2EPaths
export function withE2EPaths(env: Record<string, string | undefined>, paths: E2EPaths): Record<string, string | undefined>
