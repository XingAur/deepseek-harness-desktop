export const E2E_RUNTIME_IDENTITY_COMMAND = 'e2e_runtime_identity'

export function runtimePidFromE2eIdentity(value: unknown): number | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const runtimePid = (value as Record<string, unknown>).runtimePid
  return typeof runtimePid === 'number' && Number.isSafeInteger(runtimePid) && runtimePid > 0
    ? runtimePid
    : null
}
