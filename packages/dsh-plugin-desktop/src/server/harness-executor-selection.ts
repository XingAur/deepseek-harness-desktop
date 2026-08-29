import type { HarnessAgentExecutionContext, HarnessAgentExecutor } from '@dsh/agent-adapter/harness-host-handler'

export interface HarnessExecutorSelectionOptions {
  /** A task-selected backend wins over the process environment. */
  requestedExecutor?: string
  /** Environment-level default, normally DSH_HARNESS_EXECUTOR. */
  configuredExecutor?: string
  /** Compatibility default used when neither task nor environment chooses one. */
  defaultExecutor?: string
  executors?: Record<string, HarnessAgentExecutor | undefined>
}

export interface HarnessExecutorSelection {
  id: string
  execute: HarnessAgentExecutor
}

const EXECUTOR_ID = /^[a-z][a-z0-9._-]{1,63}$/
const HOST_BRIDGE_DEFAULT = 'host-bridge'

/**
 * Select an executor without allowing an unavailable provider to silently
 * become another provider. Harness remains the only component that can issue
 * the next decision; this function only binds one already-approved request.
 */
export function selectHarnessExecutor(options: HarnessExecutorSelectionOptions): HarnessExecutorSelection {
  const id = chooseExecutorId(options)
  const executor = options.executors?.[id]
  if (typeof executor === 'function') return { id, execute: executor }
  return {
    id,
    execute: async (_request, _context) => ({ errorCode: 'worker_backend_unavailable' }),
  }
}

export function chooseExecutorId(options: HarnessExecutorSelectionOptions): string {
  const requested = normalizeExecutorId(options.requestedExecutor)
  if (requested !== null && requested !== HOST_BRIDGE_DEFAULT) return requested
  const configured = normalizeExecutorId(options.configuredExecutor)
  if (configured !== null) return configured
  return normalizeExecutorId(options.defaultExecutor) ?? 'codex'
}

function normalizeExecutorId(value: unknown): string | null {
  if (typeof value !== 'string' || value.trim() === '') return null
  const normalized = value.trim().toLowerCase()
  return EXECUTOR_ID.test(normalized) ? normalized : null
}
