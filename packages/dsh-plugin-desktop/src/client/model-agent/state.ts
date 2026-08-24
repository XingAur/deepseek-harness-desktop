export type CredentialStatus = 'configured' | 'not-configured'

export interface ProviderMetadata {
  providerId: string
  displayName: string
  cliCommand: string
  adapterProtocol: string
  credentialSupported: boolean
  developerOnly: boolean
  credentialId?: string
  credentialStatus?: CredentialStatus
}

export interface ProviderDiagnostic {
  code: string
  message: string
}

export interface CredentialTestSummary {
  tested: boolean
  testKind: string
}

export interface ProviderStateOptions {
  developerMode?: boolean
  online?: boolean
  credentialTest?: CredentialTestSummary
}

export type ProviderStateKind =
  | 'not-configured'
  | 'configured-unverified'
  | 'invalid-credential'
  | 'network-error'
  | 'quota-exhausted'
  | 'missing-cli'
  | 'incompatible'
  | 'developer-only'
  | 'offline'
  | 'available'

export interface ProviderState {
  kind: ProviderStateKind
  label: string
  action?: 'configure' | 'test' | 'repair'
}

export function deriveProviderState(
  provider: ProviderMetadata,
  diagnostic: ProviderDiagnostic | null,
  options: ProviderStateOptions,
): ProviderState {
  if (provider.developerOnly && options.developerMode !== true) return { kind: 'developer-only', label: '开发者专用' }
  if (options.online === false) return { kind: 'offline', label: '离线', action: 'repair' }
  if (diagnostic !== null) {
    const diagnosticState = stateFromDiagnostic(diagnostic)
    if (diagnosticState !== null) return diagnosticState
  }
  if (provider.credentialSupported && provider.credentialStatus !== 'configured') {
    return { kind: 'not-configured', label: '未配置', action: 'configure' }
  }
  if (provider.credentialSupported && options.credentialTest !== undefined) {
    return { kind: 'configured-unverified', label: '已配置，未联网验证', action: 'test' }
  }
  if (provider.credentialSupported) return { kind: 'configured-unverified', label: '已配置，未验证', action: 'test' }
  return { kind: 'available', label: '可用' }
}

function stateFromDiagnostic(diagnostic: ProviderDiagnostic): ProviderState | null {
  switch (diagnostic.code) {
    case 'invalid-key': return { kind: 'invalid-credential', label: '凭证无效', action: 'configure' }
    case 'network-error': return { kind: 'network-error', label: '网络失败', action: 'repair' }
    case 'quota-exhausted': return { kind: 'quota-exhausted', label: '额度已用尽', action: 'repair' }
    case 'missing-cli': return { kind: 'missing-cli', label: '未找到 CLI', action: 'repair' }
    case 'version-too-old':
    case 'version-too-new':
    case 'unsupported-protocol': return { kind: 'incompatible', label: '版本或协议不兼容', action: 'repair' }
    default: return null
  }
}

export function messageOf(cause: unknown): string {
  if (cause instanceof Error && cause.message.length > 0) return cause.message
  if (typeof cause === 'string' && cause.length > 0) return cause
  return '模型与 Agent 服务暂时不可用'
}
