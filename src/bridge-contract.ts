export const DESKTOP_BRIDGE_CHANNEL = 'dsh-desktop/v1' as const
export const DESKTOP_BRIDGE_MAX_BYTES = 32 * 1024

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
  | 'project.directory.create'
  | 'project.directory.recycle'
  | 'external.open'
  | 'diagnostics.export'

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
  'project.directory.create': 'create_project_directory_command',
  'project.directory.recycle': 'recycle_project_directory',
  'external.open': 'open_external_https',
  'diagnostics.export': 'export_diagnostics',
} as const satisfies Record<BridgeAction, string>

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
