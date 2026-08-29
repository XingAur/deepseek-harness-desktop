import { createHash } from 'node:crypto'
import {
  HARNESS_BRIDGE_MAX_BYTES,
  type HarnessAgentRequest,
  type HarnessAgentResult,
} from './harness-bridge.js'

const MAX_PROMPT_BYTES = 48 * 1024
const MAX_EVENT_COUNT = 1_000_000
const IDENTIFIER = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/
const ERROR_CODES = new Set([
  'worker_backend_unavailable',
  'worker_backend_rejected',
  'worker_request_invalid',
  'worker_process_failed',
  'worker_protocol_invalid',
  'worker_timeout',
])

export interface HarnessAgentExecutionContext {
  readonly signal: AbortSignal
  /** Only reports execution facts; it cannot alter the Harness decision. */
  emit(payload: Record<string, unknown>): void
}

export interface HarnessAgentExecution {
  finalResponse?: Record<string, unknown>
  exitCode?: number | null
  errorCode?: string
  finalResponseValidated?: boolean
}

export type HarnessAgentExecutor = (
  request: HarnessAgentRequest,
  context: HarnessAgentExecutionContext,
) => Promise<HarnessAgentExecution>

export type HarnessEventSink = (payload: Record<string, unknown>) => void

export interface HarnessHostHandlerOptions {
  execute: HarnessAgentExecutor
}

/**
 * Host-side execution boundary for a Harness request.
 *
 * The callback receives the exact prompt decided by Harness.  There is no
 * plan/replan callback here by design: a model may execute and report facts,
 * while only Harness may issue the next decision.
 */
export function createHarnessHostHandler(options: HarnessHostHandlerOptions): (
  request: HarnessAgentRequest,
  onEvent?: HarnessEventSink,
) => Promise<HarnessAgentResult> {
  if (typeof options?.execute !== 'function') throw new TypeError('Harness 执行器无效')

  return async (rawRequest, onEvent) => {
    let request: HarnessAgentRequest
    try {
      request = validateHarnessAgentRequest(rawRequest)
    } catch {
      return failure('worker_request_invalid')
    }
    if (onEvent !== undefined && typeof onEvent !== 'function') return failure('worker_request_invalid')

    const controller = new AbortController()
    const timeoutMs = request.timeout_seconds * 1000
    let timedOut = false
    const timer = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, timeoutMs)
    let eventCount = 0
    try {
      const rawExecution: unknown = await options.execute(request, {
        signal: controller.signal,
        emit(payload) {
          if (!isSafeEvent(payload) || eventCount >= MAX_EVENT_COUNT) return
          eventCount += 1
          onEvent?.(payload)
        },
      })
      if (!isRecord(rawExecution)) return failure('worker_protocol_invalid', eventCount)
      const execution = rawExecution as HarnessAgentExecution
      if (timedOut) return failure('worker_timeout', eventCount)
      const errorCode = execution.errorCode ?? ''
      if (errorCode !== '' && !ERROR_CODES.has(errorCode)) return failure('worker_protocol_invalid', eventCount)
      const finalResponse = execution.finalResponse
      if (finalResponse !== undefined && !isRecord(finalResponse)) return failure('worker_protocol_invalid', eventCount)
      const validated = finalResponse !== undefined
        && execution.finalResponseValidated !== false
        && outputContractMatches(request, finalResponse)
      if (finalResponse !== undefined && !validated) return failure('worker_protocol_invalid', eventCount)
      const exitCode = execution.exitCode === undefined ? (errorCode === '' ? 0 : 1) : execution.exitCode
      if (exitCode !== null && (!Number.isSafeInteger(exitCode) || exitCode < -255 || exitCode > 255)) return failure('worker_protocol_invalid', eventCount)
      return {
        schema_version: 'his-agent-backend-result.v1',
        exit_code: exitCode,
        error_code: errorCode,
        event_count: eventCount,
        final_response_sha256: digest(finalResponse),
        canonical_final_response_sha256: digest(finalResponse),
        final_response_validated: validated,
        ...(finalResponse === undefined ? {} : { final_response: finalResponse }),
      }
    } catch (cause) {
      return failure(classifyFailure(cause, timedOut, controller.signal.aborted), eventCount)
    } finally {
      clearTimeout(timer)
    }
  }
}

export function validateHarnessAgentRequest(value: unknown): HarnessAgentRequest {
  if (!isRecord(value)
    || value.schema_version !== 'his-agent-backend-request.v1'
    || !['worker', 'reviewer'].includes(String(value.role))
    || typeof value.worktree_path !== 'string'
    || !value.worktree_path.startsWith('/')
    || typeof value.prompt !== 'string'
    || value.prompt.trim() === ''
    || Buffer.byteLength(value.prompt, 'utf8') > MAX_PROMPT_BYTES
    || !Number.isSafeInteger(value.timeout_seconds)
    || (value.timeout_seconds as number) < 1
    || (value.timeout_seconds as number) > 3_600
    || !isRecord(value.output_contract)
    || typeof value.output_contract.name !== 'string'
    || typeof value.output_contract.schema_version !== 'string'
    || !Array.isArray(value.capabilities)
    || value.capabilities.length > 128
    || value.capabilities.some((item) => typeof item !== 'string' || !IDENTIFIER.test(item))) {
    throw new Error('Harness Agent 请求无效')
  }
  return value as unknown as HarnessAgentRequest
}

function outputContractMatches(request: HarnessAgentRequest, response: Record<string, unknown>): boolean {
  if (request.output_contract.name === 'none' && request.output_contract.schema_version === 'none') return true
  return response.schema_version === request.output_contract.schema_version
}

function failure(errorCode: string, eventCount = 0): HarnessAgentResult {
  return {
    schema_version: 'his-agent-backend-result.v1',
    exit_code: 1,
    error_code: errorCode,
    event_count: Math.max(0, Math.min(MAX_EVENT_COUNT, eventCount)),
    final_response_sha256: '',
    canonical_final_response_sha256: '',
    final_response_validated: false,
  }
}

function classifyFailure(cause: unknown, timedOut: boolean, aborted: boolean): string {
  if (timedOut) return 'worker_timeout'
  if (aborted) return 'worker_backend_rejected'
  if (cause instanceof Error && cause.name === 'AbortError') return 'worker_timeout'
  return 'worker_process_failed'
}

function digest(value: Record<string, unknown> | undefined): string {
  if (value === undefined) return ''
  return createHash('sha256').update(canonicalJson(value), 'utf8').digest('hex')
}

function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value)
  if (Array.isArray(value)) return `[${value.map((item) => canonicalJson(item)).join(',')}]`
  const record = value as Record<string, unknown>
  return `{${Object.keys(record).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`).join(',')}}`
}

function isSafeEvent(value: unknown): value is Record<string, unknown> {
  if (!isRecord(value)) return false
  try {
    return Buffer.byteLength(JSON.stringify(value), 'utf8') <= HARNESS_BRIDGE_MAX_BYTES
      && !Object.keys(value).some((key) => /token|secret|password|authorization|payload/i.test(key))
  } catch {
    return false
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
