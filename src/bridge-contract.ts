export const DESKTOP_BRIDGE_CHANNEL = 'dsh-desktop/v1' as const
export const DESKTOP_BRIDGE_V2_CHANNEL = 'dsh-desktop/v2' as const
export const DESKTOP_BRIDGE_MAX_BYTES = 32 * 1024
export const CONTENT_REFERENCE_MAX_BYTES = 16 * 1024

export type BridgeAction =
  | 'profile.list'
  | 'profile.create'
  | 'profile.update'
  | 'profile.duplicate'
  | 'profile.delete'
  | 'profile.switch'
  | 'project.metadata.list'
  | 'project.metadata.patch'
  | 'project.metadata.remove'
  | 'project.directory.preview'
  | 'project.directory.create'
  | 'project.directory.recycle'
  | 'external.open'
  | 'diagnostics.export'
  | 'app.launch'
  | 'app.stop'
  | 'app.status'

export interface BridgeRequest {
  channel: typeof DESKTOP_BRIDGE_CHANNEL
  requestId: string
  action: BridgeAction
  payload: unknown
}

export interface BridgeError {
  code: string
  message: string
}

export interface BridgeResponse {
  channel: typeof DESKTOP_BRIDGE_CHANNEL
  requestId: string
  ok: boolean
  result?: unknown
  error?: BridgeError
}

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
  | 'harness.status'
  | 'harness.start'
  | 'harness.intake'
  | 'harness.cancel'
  | 'harness.pick-archive-root'
  | 'harness.archive-answers'
  | 'harness.connection.list'
  | 'harness.connection.save'
  | 'harness.connection.delete'
  | 'harness.connection.test'

export interface VersionedBridgeRequest {
  channel: typeof DESKTOP_BRIDGE_V2_CHANNEL
  requestId: string
  generationId: string
  sessionId: string
  action: VersionedBridgeAction
  payload: unknown
}

export interface VersionedBridgeResponse {
  channel: typeof DESKTOP_BRIDGE_V2_CHANNEL
  requestId: string
  generationId: string
  sessionId: string
  ok: boolean
  result?: unknown
  error?: BridgeError
}

export const bridgeCommandByAction = {
  'profile.list': 'list_profiles',
  'profile.create': 'create_profile',
  'profile.update': 'update_profile',
  'profile.duplicate': 'duplicate_profile',
  'profile.delete': 'delete_profile',
  'profile.switch': 'switch_profile',
  'project.metadata.list': 'list_project_metadata',
  'project.metadata.patch': 'patch_project_metadata',
  'project.metadata.remove': 'remove_project_metadata',
  'project.directory.preview': 'preview_default_project_directory',
  'project.directory.create': 'create_default_project_directory',
  'project.directory.recycle': 'recycle_project_directory',
  'external.open': 'open_external_https',
  'diagnostics.export': 'export_diagnostics',
  'app.launch': 'app_launch',
  'app.stop': 'app_stop',
  'app.status': 'app_status',
} as const satisfies Record<BridgeAction, string>

export const bridgeCommandByActionV2 = {
  'capability.inventory': 'agent_capability_inventory',
  'provider.metadata.list': 'agent_provider_metadata',
  'credential.put': 'agent_credential_put',
  'credential.delete': 'agent_credential_delete',
  'credential.status': 'agent_credential_status',
  'credential.test': 'agent_credential_test',
  'cli.path.select': 'agent_cli_path_select',
  'cli.path.status': 'agent_cli_path_status',
  'cli.install.status': 'agent_cli_install_status',
  'cli.install.start': 'agent_cli_install_start',
  'cli.login.status': 'agent_cli_login_status',
  'cli.login.start': 'agent_cli_login_start',
  'plugin.catalog.list': 'agent_plugin_catalog',
  'plugin.install.start': 'agent_plugin_install_start',
  'plugin.install.status': 'agent_plugin_install_status',
  'task.create': 'agent_task_create',
  'task.list': 'agent_task_list',
  'task.recover': 'agent_task_recover',
  'task.start': 'agent_task_start',
  'task.cancel': 'agent_task_cancel',
  'task.resume': 'agent_task_resume',
  'approval.list': 'agent_pending_approvals',
  'approval.resolve': 'agent_resolve_approval',
  'content-reference.read': 'agent_content_reference_read',
  'extension.inventory': 'agent_extension_inventory',
  'extension.install': 'agent_extension_install',
  'extension.enable': 'agent_extension_enable',
  'extension.disable': 'agent_extension_disable',
  'extension.uninstall': 'agent_extension_uninstall',
  'harness.status': 'harness_status',
  'harness.start': 'harness_start',
  'harness.intake': 'harness_intake',
  'harness.cancel': 'harness_cancel',
  'harness.pick-archive-root': 'harness_pick_archive_root',
  'harness.archive-answers': 'harness_archive_answers',
  'harness.connection.list': 'harness_connection_list',
  'harness.connection.save': 'harness_connection_save',
  'harness.connection.delete': 'harness_connection_delete',
  'harness.connection.test': 'harness_connection_test',
} as const satisfies Record<VersionedBridgeAction, string>

export function isBridgeResponse(value: unknown): value is BridgeResponse {
  if (!isRecord(value)) return false
  return value.channel === DESKTOP_BRIDGE_CHANNEL
    && validRequestId(value.requestId)
    && typeof value.ok === 'boolean'
}

export function isBridgeRequest(value: unknown): value is BridgeRequest {
  if (!isRecord(value)) return false
  return value.channel === DESKTOP_BRIDGE_CHANNEL
    && validRequestId(value.requestId)
    && typeof value.action === 'string'
    && Object.hasOwn(bridgeCommandByAction, value.action)
    && jsonSize(value) <= DESKTOP_BRIDGE_MAX_BYTES
}

export function isVersionedBridgeRequest(value: unknown): value is VersionedBridgeRequest {
  if (!isRecord(value) || !hasExactKeys(value, ['action', 'channel', 'generationId', 'payload', 'requestId', 'sessionId'])) return false
  return value.channel === DESKTOP_BRIDGE_V2_CHANNEL
    && validRequestId(value.requestId)
    && validRequestId(value.generationId)
    && validRequestId(value.sessionId)
    && typeof value.action === 'string'
    && Object.hasOwn(bridgeCommandByActionV2, value.action)
    && isVersionedBridgePayload(value.action as VersionedBridgeAction, value.payload)
    && jsonSize(value) <= DESKTOP_BRIDGE_MAX_BYTES
}

export function isVersionedBridgePayload(action: VersionedBridgeAction, value: unknown): value is Record<string, unknown> {
  if (!isRecord(value)) return false
  const allowed = versionedPayloadKeys[action]
  if (!hasExactKeys(value, [], { optional: allowed })) return false
  const hasId = (key: string) => !Object.hasOwn(value, key) || validRequestId(value[key])
  if (action === 'credential.put') {
    return hasId('credentialId')
      && hasId('providerId')
      && typeof value.secret === 'string'
      && value.secret.length > 0
      && value.secret.length <= 16 * 1024
  }
  if (action === 'cli.path.select') {
    return hasId('providerId')
      && typeof value.path === 'string'
      && value.path.length > 0
      && value.path.length <= 4096
  }
  if (action === 'content-reference.read') {
    return hasId('contentRefId')
      && hasId('taskId')
      && optionalNonNegativeInteger(value.offset, 0)
      && optionalRange(value.length, CONTENT_REFERENCE_MAX_BYTES)
  }
  if (action === 'task.create') {
    const hasProviderSelection = Object.hasOwn(value, 'providerId') || Object.hasOwn(value, 'agentId')
    return hasId('workspaceId')
      && typeof value.prompt === 'string'
      && value.prompt.trim().length > 0
      && value.prompt.length <= 16 * 1024
      && typeof value.permission === 'string'
      && ['request-approval', 'smart-approval', 'full-access'].includes(value.permission)
      && (!hasProviderSelection
        || (hasId('providerId') && hasId('agentId') && typeof value.providerId === 'string' && typeof value.agentId === 'string'))
  }
  if (action === 'approval.resolve') {
    return hasId('approvalId')
      && hasId('taskId')
      && ['allow-once', 'allow-for-task', 'deny'].includes(String(value.decision))
  }
  if (['task.start', 'task.cancel', 'task.resume'].includes(action)) return hasId('taskId')
  if (action === 'task.recover') return hasId('taskId') && hasId('workspaceId') && hasId('sourceSessionId')
  if (action === 'approval.list') return hasId('taskId')
  if (['credential.delete', 'credential.status', 'credential.test'].includes(action)) return hasId('credentialId')
  if (['cli.path.status', 'cli.install.status', 'cli.install.start', 'cli.login.status', 'cli.login.start'].includes(action)) return hasId('providerId')
  if (['extension.install', 'extension.enable', 'extension.disable', 'extension.uninstall'].includes(action)) return hasId('extensionId')
  if (action === 'harness.status' || action === 'harness.cancel' || action === 'harness.pick-archive-root') return Object.keys(value).length === 0
  if (action === 'harness.archive-answers') {
    return isAbsolutePath(value.archiveRoot)
      && typeof value.answers === 'string'
      && value.answers.trim().length > 0
      && value.answers.length <= 8000
  }
  if (action === 'harness.intake') {
    return isYunxiaoSource(value.source)
      && isAbsolutePath(value.archiveRoot)
      && (value.includeComments === undefined || typeof value.includeComments === 'boolean')
      && (value.yunxiaoProfileId === undefined || validRequestId(value.yunxiaoProfileId))
      && (value.selectedModelId === undefined || hasId('selectedModelId'))
      && (value.agentBackend === undefined
        || (typeof value.agentBackend === 'string' && /^[a-z][a-z0-9._-]{0,63}$/.test(value.agentBackend)))
  }
  if (action === 'harness.connection.list') {
    return value.kind === undefined || value.kind === 'mcp' || value.kind === 'database'
  }
  if (action === 'harness.connection.delete' || action === 'harness.connection.test') {
    return hasId('profileId')
  }
  if (action === 'harness.connection.save') {
    return hasId('profileId')
      && (value.kind === 'mcp' || value.kind === 'database')
      && (value.providerId === undefined || value.providerId === 'yunxiao' || value.providerId === 'gitlab' || value.providerId === 'generic')
      && typeof value.displayName === 'string'
      && value.displayName.trim().length > 0
      && value.displayName.length <= 120
      && typeof value.endpoint === 'string'
      && value.endpoint.length <= 4096
      && typeof value.readOnly === 'boolean'
      && typeof value.enabled === 'boolean'
      && (value.credentialId === undefined || hasId('credentialId'))
  }
  if (action === 'harness.start') {
    const hasLegacyTaskPackage = isAbsolutePath(value.taskContractPath) && isAbsolutePath(value.understandingPath)
    const hasArchiveTaskPackage = isAbsolutePath(value.archiveRoot)
    return (hasLegacyTaskPackage || hasArchiveTaskPackage)
      && (value.taskContractPath === undefined || isAbsolutePath(value.taskContractPath))
      && (value.understandingPath === undefined || isAbsolutePath(value.understandingPath))
      && isAbsolutePath(value.worktreeRoot)
      && isAbsolutePath(value.knowledgeHome)
      && typeof value.authorizationId === 'string'
      && /^[A-Za-z0-9._-]{1,256}$/.test(value.authorizationId)
      && (value.agentBackend === undefined
        || (typeof value.agentBackend === 'string' && /^[a-z0-9._-]{1,64}$/.test(value.agentBackend)))
      && (value.archiveRoot === undefined || isAbsolutePath(value.archiveRoot))
      && (value.selectedModelId === undefined || hasId('selectedModelId'))
      && (value.yunxiaoProfileId === undefined || hasId('yunxiaoProfileId'))
      && (value.gitlabProfileId === undefined || hasId('gitlabProfileId'))
      && (value.databaseProfileId === undefined || hasId('databaseProfileId'))
  }
  if (action === 'plugin.catalog.list') {
    return (value.query === undefined || (typeof value.query === 'string' && value.query.length <= 120))
      && (value.category === undefined || (typeof value.category === 'string' && value.category.length <= 64))
      && optionalNonNegativeInteger(value.offset, 0)
      && optionalRange(value.limit ?? 50, 50)
      && (value.refresh === undefined || typeof value.refresh === 'boolean')
  }
  if (action === 'plugin.install.start' || action === 'plugin.install.status') {
    return typeof value.pluginId === 'string' && validPluginId(value.pluginId)
  }
  return Object.keys(value).length === 0
}

function isAbsolutePath(value: unknown): value is string {
  return typeof value === 'string'
    && value.length > 0
    && value.length <= 4096
    && (value.startsWith('/') || /^[A-Za-z]:[\\/]/.test(value))
}

function isYunxiaoSource(value: unknown): value is string {
  if (typeof value !== 'string' || value.length === 0 || value.length > 4096 || /[?#\u0000]/.test(value)) return false
  return /^[A-Za-z][A-Za-z0-9]{1,31}-\d{1,20}$/.test(value)
    || /^https:\/\/[^/\s]+(?:\/[^\s]*)?$/.test(value)
}

export function isVersionedBridgeResponse(value: unknown): value is VersionedBridgeResponse {
  if (!isRecord(value)) return false
  const hasResult = Object.hasOwn(value, 'result')
  const hasError = Object.hasOwn(value, 'error')
  if (!hasExactKeys(value, [
    'channel',
    'generationId',
    'ok',
    'requestId',
    'sessionId',
  ], { optional: ['error', 'result'] })) return false
  if (value.channel !== DESKTOP_BRIDGE_V2_CHANNEL
    || !validRequestId(value.requestId)
    || !validRequestId(value.generationId)
    || !validRequestId(value.sessionId)
    || typeof value.ok !== 'boolean'
    || (hasResult && hasError)
    || (value.ok && !hasResult)
    || (!value.ok && !hasError)
    || (hasResult && containsSecretShape(value.result))) return false
  if (hasError && !isBridgeError(value.error)) return false
  return jsonSize(value) <= DESKTOP_BRIDGE_MAX_BYTES
}

export function containsSecretShape(value: unknown): boolean {
  if (Array.isArray(value)) return value.some(containsSecretShape)
  if (!isRecord(value)) return false
  return Object.entries(value).some(([key, nested]) => {
    if (/^(api[_-]?key|access[_-]?token|refresh[_-]?token|token|oauth|authorization|cookie|set-cookie|secret|password|private[_-]?key)$/i.test(key)) return true
    return containsSecretShape(nested)
  })
}

function validPluginId(value: string): boolean {
  return /^[A-Za-z0-9][A-Za-z0-9._/-]{0,159}$/.test(value)
}

export function validRequestId(value: unknown): value is string {
  return typeof value === 'string' && /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(value)
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function jsonSize(value: unknown): number {
  try {
    return new TextEncoder().encode(JSON.stringify(value)).byteLength
  } catch {
    return Number.POSITIVE_INFINITY
  }
}

function hasExactKeys(
  value: Record<string, unknown>,
  required: string[],
  options: { optional?: string[] } = {},
): boolean {
  const optional = new Set(options.optional ?? [])
  const allowed = new Set([...required, ...optional])
  const keys = Object.keys(value)
  return required.every((key) => Object.hasOwn(value, key))
    && keys.every((key) => allowed.has(key))
    && keys.length >= required.length
}

function isBridgeError(value: unknown): value is BridgeError {
  return isRecord(value)
    && hasExactKeys(value, ['code', 'message'])
    && typeof value.code === 'string'
    && typeof value.message === 'string'
    && value.code.length <= 128
    && value.message.length <= 1024
}

const versionedPayloadKeys: Record<VersionedBridgeAction, string[]> = {
  'capability.inventory': [],
  'provider.metadata.list': [],
  'credential.put': ['credentialId', 'providerId', 'secret'],
  'credential.delete': ['credentialId'],
  'credential.status': ['credentialId'],
  'credential.test': ['credentialId'],
  'cli.path.select': ['providerId', 'path'],
  'cli.path.status': ['providerId'],
  'cli.install.status': ['providerId'],
  'cli.install.start': ['providerId'],
  'cli.login.status': ['providerId'],
  'cli.login.start': ['providerId'],
  'plugin.catalog.list': ['query', 'category', 'offset', 'limit', 'refresh'],
  'plugin.install.start': ['pluginId'],
  'plugin.install.status': ['pluginId'],
  'task.create': ['workspaceId', 'prompt', 'permission', 'providerId', 'agentId'],
  'task.list': ['workspaceId'],
  'task.recover': ['workspaceId', 'taskId', 'sourceSessionId'],
  'task.start': ['taskId'],
  'task.cancel': ['taskId'],
  'task.resume': ['taskId'],
  'approval.list': ['taskId'],
  'approval.resolve': ['approvalId', 'taskId', 'decision'],
  'content-reference.read': ['contentRefId', 'taskId', 'offset', 'length'],
  'extension.inventory': [],
  'extension.install': ['extensionId'],
  'extension.enable': ['extensionId'],
  'extension.disable': ['extensionId'],
  'extension.uninstall': ['extensionId'],
  'harness.status': [],
  'harness.start': [
    'taskContractPath', 'understandingPath', 'worktreeRoot', 'knowledgeHome', 'authorizationId', 'agentBackend',
    'archiveRoot', 'selectedModelId', 'yunxiaoProfileId', 'gitlabProfileId', 'databaseProfileId',
  ],
  'harness.intake': ['source', 'archiveRoot', 'includeComments', 'yunxiaoProfileId', 'selectedModelId', 'agentBackend'],
  'harness.cancel': [],
  'harness.pick-archive-root': [],
  'harness.archive-answers': ['archiveRoot', 'answers'],
  'harness.connection.list': ['kind'],
  'harness.connection.save': ['profileId', 'kind', 'providerId', 'displayName', 'endpoint', 'readOnly', 'enabled', 'credentialId'],
  'harness.connection.delete': ['profileId'],
  'harness.connection.test': ['profileId'],
}

function optionalRange(value: unknown, defaultValue: number): boolean {
  const candidate = value ?? defaultValue
  return optionalNonNegativeInteger(candidate) && (candidate as number) <= defaultValue
}

function optionalNonNegativeInteger(value: unknown, defaultValue?: number): boolean {
  const candidate = value ?? defaultValue
  return Number.isSafeInteger(candidate) && (candidate as number) >= 0
}
