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
  | 'skill.create'
  | 'skill.import'
  | 'prompts.list'
  | 'prompts.get'
  | 'prompts.save'
  | 'prompts.resolve-conflict'
  | 'prompts.delete'
  | 'prompts.activate'
  | 'prompts.deactivate'
  | 'prompts.status'
  | 'prompts.import'
  | 'mcp.list'
  | 'mcp.upsert'
  | 'mcp.delete'
  | 'mcp.sync'
  | 'mcp.import'
  | 'mcp.status'
  | 'skills.list'
  | 'skills.install.zip'
  | 'skills.uninstall'
  | 'skills.sync'
  | 'harness.status'
  | 'harness.start'
  | 'harness.chat.start'
  | 'harness.pick-evidence-files'
  | 'harness.intake'
  | 'harness.cancel'
  | 'harness.pick-archive-root'
  | 'harness.archive-answers'
  | 'harness.connection.list'
  | 'harness.connection.save'
  | 'harness.connection.delete'
  | 'harness.connection.test'

interface VersionedBridgeResponse {
  channel: typeof DESKTOP_BRIDGE_V2_CHANNEL
  requestId: string
  generationId: string
  sessionId: string
  ok: boolean
  result?: unknown
  error?: { code: string; message: string }
}

export interface VersionedResponseOptions {
  /** MCP 服务器定义(列表/保存/导入)的 result 携带用户自录的 env 配置,不是凭证库机密。 */
  allowSecretShapedResult?: boolean
}

/** 这些动作的 result 是用户在同一扩展中心界面录入的 MCP 服务器定义(含 env),豁免 secret 形状拦截。 */
export function allowsSecretShapedResult(action: VersionedBridgeAction): boolean {
  return action === 'mcp.list' || action === 'mcp.upsert' || action === 'mcp.import'
}

export function isVersionedBridgeResponse(value: unknown, options: VersionedResponseOptions = {}): value is VersionedBridgeResponse {
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
    || (hasResult && containsSecretShape(value.result, { exemptEnv: options.allowSecretShapedResult }))) return false
  if (hasError && !isBridgeError(value.error)) return false
  return byteSize(value) <= MAX_BYTES
}

function containsSecretShape(value: unknown, options: { exemptEnv?: boolean } = {}): boolean {
  if (Array.isArray(value)) return value.some((item) => containsSecretShape(item, options))
  if (!isRecord(value)) return false
  return Object.entries(value).some(([key, nested]) => {
    if (/^(api[_-]?key|access[_-]?token|refresh[_-]?token|token|oauth|authorization|cookie|set-cookie|secret|password|private[_-]?key)$/i.test(key)) return true
    // MCP 服务器定义豁免:env 子树是用户自录的普通配置(如 API_KEY),不视作凭证库机密外泄。
    if (options.exemptEnv && key === 'env') return false
    return containsSecretShape(nested, options)
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
