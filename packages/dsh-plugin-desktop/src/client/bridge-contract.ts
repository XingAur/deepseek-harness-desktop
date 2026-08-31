export const DESKTOP_BRIDGE_V2_CHANNEL = 'dsh-desktop/v2' as const
const MAX_BYTES = 32 * 1024

export type VersionedBridgeAction =
  | 'capability.inventory'
  | 'provider.metadata.list'
  | 'credential.put'
  | 'credential.delete'
  | 'credential.status'
  | 'credential.test'
  | 'cli.path.select'
  | 'cli.path.status'
  | 'cli.install.status'
  | 'cli.install.start'
  | 'cli.login.status'
  | 'cli.login.start'
  | 'plugin.catalog.list'
  | 'plugin.install.start'
  | 'plugin.install.status'
  | 'task.create'
  | 'task.list'
  | 'task.recover'
  | 'task.start'
  | 'task.cancel'
  | 'task.resume'
  | 'approval.list'
  | 'approval.resolve'
  | 'content-reference.read'
  | 'extension.inventory'
  | 'extension.install'
  | 'extension.enable'
  | 'extension.disable'
  | 'extension.uninstall'
  | 'prompts.list'
  | 'prompts.get'
  | 'prompts.save'
  | 'prompts.resolve-conflict'
  | 'prompts.delete'
  | 'prompts.activate'
  | 'prompts.deactivate'
  | 'prompts.status'
  | 'prompts.import'

interface VersionedBridgeResponse {
  channel: typeof DESKTOP_BRIDGE_V2_CHANNEL
  requestId: string
  generationId: string
  sessionId: string
  ok: boolean
  result?: unknown
  error?: { code: string; message: string }
}

export function isVersionedBridgeResponse(value: unknown): value is VersionedBridgeResponse {
  if (!isRecord(value)) return false
  const hasResult = Object.hasOwn(value, 'result')
  const hasError = Object.hasOwn(value, 'error')
  if (!hasExactKeys(value, ['channel', 'generationId', 'ok', 'requestId', 'sessionId'], ['error', 'result'])) return false
  if (value.channel !== DESKTOP_BRIDGE_V2_CHANNEL
    || !validId(value.requestId)
    || !validId(value.generationId)
    || !validId(value.sessionId)
    || typeof value.ok !== 'boolean'
    || (hasResult && hasError)
    || (value.ok && !hasResult)
    || (!value.ok && !hasError)
    || (hasResult && containsSecretShape(value.result))) return false
  if (hasError && !isBridgeError(value.error)) return false
  return byteSize(value) <= MAX_BYTES
}

function containsSecretShape(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(containsSecretShape)
  if (!isRecord(value)) return false
  return Object.entries(value).some(([key, nested]) => {
    if (/^(api[_-]?key|access[_-]?token|refresh[_-]?token|token|oauth|authorization|cookie|set-cookie|secret|password|private[_-]?key)$/i.test(key)) return true
    return containsSecretShape(nested)
  })
}

function validId(value: unknown): value is string {
  return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasExactKeys(value: Record<string, unknown>, required: string[], optional: string[]): boolean {
  const allowed = new Set([...required, ...optional])
  return required.every((key) => Object.hasOwn(value, key))
    && Object.keys(value).every((key) => allowed.has(key))
}

function isBridgeError(value: unknown): value is { code: string; message: string } {
  return isRecord(value)
    && hasExactKeys(value, ['code', 'message'], [])
    && typeof value.code === 'string'
    && typeof value.message === 'string'
    && value.code.length <= 128
    && value.message.length <= 1024
}

function byteSize(value: unknown): number {
  try {
    return new TextEncoder().encode(JSON.stringify(value)).byteLength
  } catch {
    return Number.POSITIVE_INFINITY
  }
}
