export type ConnectionKind = 'mcp' | 'http-api' | 'database'
export type ConnectionTransport = 'stdio' | 'http' | 'sse' | 'database'
export type WorkingDirectoryPolicy = 'workspace' | 'inherit' | 'none'
export type DatabaseType = 'postgresql' | 'mysql' | 'sqlserver' | 'oracle'
export type ConnectionLayerId = 'configuration' | 'network' | 'protocol' | 'authentication' | 'permission'
export type ConnectionLayerState = 'passed' | 'failed' | 'not-configured' | 'not-tested' | 'approval-required'

export interface ConnectionTestLayer {
  id: ConnectionLayerId
  label: string
  state: ConnectionLayerState
  message: string
}

export interface ConnectionTestResult {
  summary: string
  layers: ConnectionTestLayer[]
}

export interface ConnectionProfile {
  profileId: string
  kind: ConnectionKind
  transport: ConnectionTransport
  source: 'custom' | 'legacy'
  templateId: string
  providerId?: string
  displayName: string
  endpoint: string
  command: string
  args: string[]
  environmentKeys: string[]
  workingDirectoryPolicy: WorkingDirectoryPolicy
  healthPath: string
  readOnly: boolean
  enabled: boolean
  credentialId?: string
  databaseType?: DatabaseType
  host?: string
  port?: number
  databaseName?: string
  username?: string
  encoding?: string
  testQuery?: string
  latestTest: ConnectionTestResult
}

export type ConnectionDraft = Omit<ConnectionProfile, 'profileId' | 'source' | 'latestTest'> & { profileId?: string }

const UNTESTED_RESULT: ConnectionTestResult = {
  summary: '未测试',
  layers: [
    { id: 'configuration', label: '配置', state: 'not-tested', message: '尚未校验' },
    { id: 'network', label: '网络', state: 'not-tested', message: '尚未探测' },
    { id: 'protocol', label: '协议', state: 'not-tested', message: '尚未验证' },
    { id: 'authentication', label: '认证', state: 'not-tested', message: '尚未验证' },
    { id: 'permission', label: '权限', state: 'not-tested', message: '尚未评估' },
  ],
}

export function createConnectionDraft(kind: ConnectionKind, transport: ConnectionTransport): ConnectionDraft {
  const base: ConnectionDraft = {
    kind,
    transport,
    templateId: kind === 'database' ? 'database' : 'custom',
    displayName: '',
    endpoint: '',
    command: '',
    args: [],
    environmentKeys: [],
    workingDirectoryPolicy: transport === 'stdio' ? 'workspace' : 'none',
    healthPath: '',
    readOnly: true,
    enabled: true,
  }
  if (kind !== 'database') return base
  return {
    ...base,
    databaseType: 'postgresql',
    host: 'localhost',
    port: 5432,
    databaseName: '',
    username: '',
    encoding: 'UTF-8',
    testQuery: 'SELECT 1',
  }
}

export function editConnectionDraft(profile: ConnectionProfile): ConnectionDraft {
  return {
    profileId: profile.profileId,
    kind: profile.kind,
    transport: profile.transport,
    templateId: profile.templateId,
    ...(profile.providerId === undefined ? {} : { providerId: profile.providerId }),
    displayName: profile.displayName,
    endpoint: profile.endpoint,
    command: profile.command,
    args: [...profile.args],
    environmentKeys: [...profile.environmentKeys],
    workingDirectoryPolicy: profile.workingDirectoryPolicy,
    healthPath: profile.healthPath,
    readOnly: profile.readOnly,
    enabled: profile.enabled,
    ...(profile.credentialId === undefined ? {} : { credentialId: profile.credentialId }),
    ...(profile.kind !== 'database' ? {} : {
      databaseType: profile.databaseType ?? 'postgresql',
      host: profile.host ?? endpointPart(profile.endpoint, 'hostname') ?? 'localhost',
      port: profile.port ?? numericEndpointPort(profile.endpoint) ?? defaultDatabasePort(profile.databaseType ?? 'postgresql'),
      databaseName: profile.databaseName ?? endpointPart(profile.endpoint, 'pathname')?.replace(/^\//, '') ?? '',
      username: profile.username ?? '',
      encoding: profile.encoding ?? 'UTF-8',
      testQuery: profile.testQuery ?? 'SELECT 1',
    }),
  }
}

export function prepareConnectionDraft(draft: ConnectionDraft): ConnectionDraft {
  if (draft.kind !== 'database') return draft
  const databaseType = draft.databaseType ?? 'postgresql'
  const host = draft.host?.trim() ?? ''
  const port = draft.port ?? defaultDatabasePort(databaseType)
  const databaseName = draft.databaseName?.trim() ?? ''
  return {
    ...draft,
    databaseType,
    host,
    port,
    databaseName,
    username: draft.username?.trim() ?? '',
    encoding: draft.encoding?.trim() || 'UTF-8',
    testQuery: draft.testQuery?.trim() || 'SELECT 1',
    endpoint: `${databaseType}://${host}:${port}/${encodeURIComponent(databaseName)}`,
  }
}

export function validateConnectionDraft(draft: ConnectionDraft): string[] {
  draft = prepareConnectionDraft(draft)
  const errors: string[] = []
  if (draft.displayName.trim() === '' || draft.displayName.length > 120) errors.push('连接名称不能为空且不能超过 120 个字符')
  if (!transportMatchesKind(draft.kind, draft.transport)) errors.push('连接类型与传输方式不匹配')
  if (draft.transport === 'stdio') {
    if (draft.command.trim() === '') errors.push('MCP stdio 连接必须填写命令')
    if (draft.command.length > 4096 || /[\u0000\r\n]/.test(draft.command)) errors.push('命令格式无效')
  } else if (draft.kind === 'database') {
    if (!['postgresql', 'mysql', 'sqlserver', 'oracle'].includes(draft.databaseType ?? '')) errors.push('请选择受支持的数据库类型')
    if ((draft.host ?? '').trim() === '' || /[\s/@?#\u0000]/.test(draft.host ?? '')) errors.push('数据库主机格式无效')
    if (!Number.isInteger(draft.port) || (draft.port ?? 0) < 1 || (draft.port ?? 0) > 65535) errors.push('数据库端口必须在 1 到 65535 之间')
    if ((draft.databaseName ?? '').trim() === '') errors.push('数据库名称不能为空')
    if ((draft.username ?? '').trim() === '') errors.push('数据库用户名不能为空')
    if ((draft.testQuery ?? '').length > 512 || /[\u0000\r\n]/.test(draft.testQuery ?? '')) errors.push('连接测试查询语句格式无效')
    if (!safeEndpoint(draft.endpoint, draft.transport)) errors.push('数据库连接字段格式无效')
  } else {
    if (!safeEndpoint(draft.endpoint, draft.transport)) errors.push('仅允许 HTTPS 地址，回环地址可使用 HTTP')
  }
  if (draft.args.length > 32 || draft.args.some((item) => item.length > 512 || /[\u0000\r\n]/.test(item))) errors.push('命令参数超出安全限制')
  if (draft.environmentKeys.length > 32 || draft.environmentKeys.some((item) => !/^[A-Za-z_][A-Za-z0-9_]{0,127}$/.test(item))) {
    errors.push('环境变量只能填写名称，不能填写值')
  }
  if (draft.healthPath !== '' && (!draft.healthPath.startsWith('/') || draft.healthPath.length > 512 || /[\u0000-\u001f\u007f]/.test(draft.healthPath))) {
    errors.push('健康检查路径必须以 / 开头')
  }
  return errors
}

export function normalizeConnectionProfile(value: Partial<ConnectionProfile> & Pick<ConnectionProfile, 'profileId' | 'kind' | 'displayName'>): ConnectionProfile {
  const legacy = value.source === undefined
  const transport = value.transport ?? (value.kind === 'database' ? 'database' : 'http')
  const templateId = value.templateId ?? value.providerId ?? 'custom'
  return {
    profileId: value.profileId,
    kind: value.kind,
    transport,
    source: value.source ?? 'legacy',
    templateId,
    ...(value.providerId === undefined ? {} : { providerId: value.providerId }),
    displayName: value.displayName,
    endpoint: value.endpoint ?? '',
    command: value.command ?? '',
    args: value.args ?? [],
    environmentKeys: value.environmentKeys ?? [],
    workingDirectoryPolicy: value.workingDirectoryPolicy ?? (transport === 'stdio' ? 'workspace' : 'none'),
    healthPath: value.healthPath ?? '',
    readOnly: value.readOnly ?? true,
    enabled: value.enabled ?? true,
    ...(value.credentialId === undefined ? {} : { credentialId: value.credentialId }),
    ...(value.kind !== 'database' ? {} : {
      databaseType: value.databaseType ?? databaseTypeFromEndpoint(value.endpoint) ?? 'postgresql',
      host: value.host ?? endpointPart(value.endpoint, 'hostname') ?? 'localhost',
      port: value.port ?? numericEndpointPort(value.endpoint) ?? defaultDatabasePort(value.databaseType ?? databaseTypeFromEndpoint(value.endpoint) ?? 'postgresql'),
      databaseName: value.databaseName ?? decodeURIComponent(endpointPart(value.endpoint, 'pathname')?.replace(/^\//, '') ?? ''),
      username: value.username ?? '',
      encoding: value.encoding ?? 'UTF-8',
      testQuery: value.testQuery ?? 'SELECT 1',
    }),
    latestTest: value.latestTest ?? {
      summary: '未测试',
      layers: legacy
        ? UNTESTED_RESULT.layers.map((layer) => ({ ...layer, message: layer.id === 'network' ? '兼容连接尚未执行网络探测' : '兼容连接未提供此层结果' }))
        : UNTESTED_RESULT.layers.map((layer) => ({ ...layer })),
    },
  }
}

export function defaultDatabasePort(type: DatabaseType): number {
  if (type === 'mysql') return 3306
  if (type === 'sqlserver') return 1433
  if (type === 'oracle') return 1521
  return 5432
}

function databaseTypeFromEndpoint(value?: string): DatabaseType | undefined {
  try {
    const protocol = new URL(value ?? '').protocol.replace(':', '')
    return ['postgresql', 'mysql', 'sqlserver', 'oracle'].includes(protocol) ? protocol as DatabaseType : undefined
  } catch { return undefined }
}

function endpointPart(value: string | undefined, part: 'hostname' | 'pathname'): string | undefined {
  try { return new URL(value ?? '')[part] || undefined } catch { return undefined }
}

function numericEndpointPort(value?: string): number | undefined {
  try {
    const raw = new URL(value ?? '').port
    return raw === '' ? undefined : Number(raw)
  } catch { return undefined }
}

function transportMatchesKind(kind: ConnectionKind, transport: ConnectionTransport): boolean {
  if (kind === 'mcp') return transport === 'stdio' || transport === 'http' || transport === 'sse'
  if (kind === 'http-api') return transport === 'http'
  return transport === 'database'
}

function safeEndpoint(value: string, transport: ConnectionTransport): boolean {
  if (value.length === 0 || value.length > 4096 || /[\u0000-\u001f\u007f]/.test(value)) return false
  try {
    const parsed = new URL(value)
    if (parsed.username !== '' || parsed.password !== '') return false
    if (transport === 'database') return true
    const loopback = parsed.hostname === '127.0.0.1' || parsed.hostname === 'localhost' || parsed.hostname === '::1'
    return parsed.protocol === 'https:' || (parsed.protocol === 'http:' && loopback)
  } catch {
    return false
  }
}
