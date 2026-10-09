export interface BilingualText { readonly en: string; readonly zh: string }
export interface McpTemplateFieldOption { readonly value: string; readonly label: BilingualText }
export interface McpTemplateField {
  readonly key: string
  readonly label: BilingualText
  readonly placeholder?: BilingualText
  readonly secret?: boolean
  readonly required?: boolean
  readonly initial?: string
  readonly options?: readonly McpTemplateFieldOption[]
  readonly visibleWhen?: (values: Readonly<Record<string, string>>) => boolean
}
export type McpTemplateIcon = 'database' | 'git' | 'cloud' | 'globe' | 'folder' | 'brain'
export type McpTemplateBuild =
  | { readonly transport: 'stdio'; readonly command: string; readonly args: readonly string[]; readonly env: Readonly<Record<string, string>> }
  | { readonly transport: 'streamable-http'; readonly url: string; readonly headers: Readonly<Record<string, string>> }
export interface McpTemplate {
  readonly id: string
  readonly name: BilingualText
  readonly serverName: string
  readonly icon: McpTemplateIcon
  readonly description: BilingualText
  readonly fields: readonly McpTemplateField[]
  readonly build: (values: Readonly<Record<string, string>>) => McpTemplateBuild
}

const YUNXIAO_MCP_URL = 'https://openapi-rdc.aliyuncs.com/ai/mcp?toolsets=organization-management,project-management'
const DB_TYPES: readonly McpTemplateFieldOption[] = [
  { value: 'postgresql', label: { en: 'PostgreSQL', zh: 'PostgreSQL' } },
  { value: 'mysql', label: { en: 'MySQL', zh: 'MySQL' } },
  { value: 'oracle', label: { en: 'Oracle', zh: 'Oracle' } },
  { value: 'sqlserver', label: { en: 'SQL Server', zh: 'SQL Server' } },
  { value: 'sqlite', label: { en: 'SQLite', zh: 'SQLite' } },
]
const DB_PORTS: Readonly<Record<string, string>> = { postgresql: '5432', mysql: '3306', oracle: '1521', sqlserver: '1433' }
const trimmed = (values: Readonly<Record<string, string>>, key: string): string => (values[key] ?? '').trim()
const isNetworkDatabase = (values: Readonly<Record<string, string>>): boolean => trimmed(values, 'dbtype') !== 'sqlite'
const databasePort = (values: Readonly<Record<string, string>>): string => trimmed(values, 'port') || DB_PORTS[trimmed(values, 'dbtype')] || ''

function databaseBuild(values: Readonly<Record<string, string>>): McpTemplateBuild {
  const type = trimmed(values, 'dbtype')
  if (type === 'sqlite') {
    return { transport: 'stdio', command: 'npx', args: ['-y', '@executeautomation/database-server', trimmed(values, 'file')], env: {} }
  }
  const host = trimmed(values, 'host')
  const port = databasePort(values)
  const database = trimmed(values, 'database')
  const env = { DSH_DB_USER: trimmed(values, 'user'), DSH_DB_PASSWORD: trimmed(values, 'password') }
  if (type === 'oracle') {
    return {
      transport: 'stdio', command: 'uvx', env,
      args: ['mcp-server-oracle', '--user', '${DSH_DB_USER}', '--password', '${DSH_DB_PASSWORD}', '--dsn', `${host}:${port}/${database}`],
    }
  }
  const mode = type === 'sqlserver' ? '--sqlserver' : type === 'mysql' ? '--mysql' : '--postgresql'
  const hostFlag = type === 'sqlserver' ? '--server' : '--host'
  return {
    transport: 'stdio', command: 'npx', env,
    args: ['-y', '@executeautomation/database-server', mode, hostFlag, host, '--port', port, '--database', database, '--user', '${DSH_DB_USER}', '--password', '${DSH_DB_PASSWORD}'],
  }
}

export const MCP_TEMPLATES: readonly McpTemplate[] = [
  {
    id: 'database', name: { en: 'Database', zh: '数据库' }, serverName: 'database', icon: 'database',
    description: { en: 'Choose a database type, then fill only its connection information.', zh: '只需选择数据库类型，再填写连接信息。' },
    fields: [
      { key: 'dbtype', label: { en: 'Database type', zh: '数据库类型' }, options: DB_TYPES, initial: 'postgresql', required: true },
      { key: 'host', label: { en: 'Host', zh: '地址' }, placeholder: { en: '192.168.1.10', zh: '192.168.1.10' }, required: true, visibleWhen: isNetworkDatabase },
      { key: 'port', label: { en: 'Port (optional)', zh: '端口（可不填）' }, visibleWhen: isNetworkDatabase },
      { key: 'user', label: { en: 'Username', zh: '用户名' }, required: true, visibleWhen: isNetworkDatabase },
      { key: 'password', label: { en: 'Password', zh: '密码' }, secret: true, required: true, visibleWhen: isNetworkDatabase },
      { key: 'database', label: { en: 'Database / service name', zh: '数据库名 / 服务名' }, required: true, visibleWhen: isNetworkDatabase },
      { key: 'file', label: { en: 'SQLite file', zh: 'SQLite 文件路径' }, placeholder: { en: '/Users/you/data.db', zh: '/Users/你/data.db' }, required: true, visibleWhen: values => !isNetworkDatabase(values) },
    ],
    build: databaseBuild,
  },
  {
    id: 'yunxiao', name: { en: 'Yunxiao', zh: '云效' }, serverName: 'yunxiao', icon: 'cloud',
    description: { en: 'Query, comment, and update Aliyun Yunxiao work items.', zh: '查询、评论和更新阿里云云效工作项。' },
    fields: [{ key: 'token', label: { en: 'Yunxiao access token', zh: '云效个人访问令牌' }, secret: true, required: true }],
    build: values => ({ transport: 'streamable-http', url: YUNXIAO_MCP_URL, headers: { Authorization: `Bearer ${trimmed(values, 'token')}` } }),
  },
  {
    id: 'gitlab', name: { en: 'GitLab', zh: 'GitLab' }, serverName: 'gitlab', icon: 'git',
    description: { en: 'Browse projects, issues, merge requests, and code.', zh: '访问项目、Issue、合并请求和代码。' },
    fields: [
      { key: 'host', label: { en: 'GitLab URL', zh: 'GitLab 地址' }, initial: 'https://gitlab.com', required: true },
      { key: 'token', label: { en: 'Personal access token', zh: '个人访问令牌' }, secret: true, required: true },
    ],
    build: values => ({ transport: 'stdio', command: 'npx', args: ['-y', '@zereight/mcp-gitlab'], env: { GITLAB_PERSONAL_ACCESS_TOKEN: trimmed(values, 'token'), GITLAB_API_URL: `${trimmed(values, 'host').replace(/\/+$/u, '')}/api/v4` } }),
  },
  {
    id: 'playwright', name: { en: 'Browser', zh: '浏览器' }, serverName: 'browser', icon: 'globe',
    description: { en: 'Open pages, click, capture screenshots, and inspect web apps.', zh: '打开网页、点击、截图和检查 Web 应用。' }, fields: [],
    build: () => ({ transport: 'stdio', command: 'npx', args: ['-y', '@playwright/mcp@latest'], env: {} }),
  },
  {
    id: 'filesystem', name: { en: 'Files', zh: '文件' }, serverName: 'files', icon: 'folder',
    description: { en: 'Allow access to one selected folder.', zh: '允许模型访问一个指定目录。' },
    fields: [{ key: 'dir', label: { en: 'Allowed folder', zh: '允许访问的目录' }, placeholder: { en: '/Users/you/Documents', zh: '/Users/你/Documents' }, required: true }],
    build: values => ({ transport: 'stdio', command: 'npx', args: ['-y', '@modelcontextprotocol/server-filesystem', trimmed(values, 'dir')], env: {} }),
  },
  {
    id: 'memory', name: { en: 'Memory', zh: '记忆' }, serverName: 'memory', icon: 'brain',
    description: { en: 'Local knowledge graph shared across sessions.', zh: '可跨会话使用的本地知识图谱。' }, fields: [],
    build: () => ({ transport: 'stdio', command: 'npx', args: ['-y', '@modelcontextprotocol/server-memory'], env: {} }),
  },
  {
    id: 'http-generic', name: { en: 'Remote MCP', zh: '远程 MCP' }, serverName: 'remote-mcp', icon: 'globe',
    description: { en: 'Connect any Streamable HTTP MCP endpoint.', zh: '连接任意 Streamable HTTP MCP 地址。' },
    fields: [
      { key: 'url', label: { en: 'MCP URL', zh: 'MCP 地址' }, placeholder: { en: 'https://host/mcp', zh: 'https://host/mcp' }, required: true },
      { key: 'headerName', label: { en: 'Header name', zh: '请求头名称' }, initial: 'Authorization' },
      { key: 'headerValue', label: { en: 'Header value', zh: '请求头值' }, secret: true },
    ],
    build: values => {
      const name = trimmed(values, 'headerName'); const value = trimmed(values, 'headerValue')
      return { transport: 'streamable-http', url: trimmed(values, 'url'), headers: name && value ? { [name]: value } : {} }
    },
  },
]

export function templateInitialValues(template: McpTemplate): Record<string, string> {
  return Object.fromEntries(template.fields.map(field => [field.key, field.initial ?? '']))
}
export function templateRequirementsMet(template: McpTemplate, values: Readonly<Record<string, string>>): boolean {
  return template.fields.every(field => field.visibleWhen?.(values) === false || field.required !== true || trimmed(values, field.key).length > 0)
}
export function templateText(text: BilingualText): string {
  return typeof navigator !== 'undefined' && !navigator.language.toLowerCase().startsWith('zh') ? text.en : text.zh
}
