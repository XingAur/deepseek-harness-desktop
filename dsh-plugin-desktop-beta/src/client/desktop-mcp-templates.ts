/**
 * Friendly MCP connection templates: one card per common system where the
 * user fills plain credentials (host, port, token) and the template assembles
 * the concrete MCP row. Pure data plus pure builders; no React.
 */

/** Bilingual inline copy for template chrome (labels render in app language). */
export interface BilingualText {
  readonly en: string
  readonly zh: string
}

/** One friendly input on a template form. */
export interface McpTemplateFieldOption {
  readonly value: string
  readonly label: BilingualText
}

export interface McpTemplateField {
  readonly key: string
  readonly label: BilingualText
  readonly placeholder?: BilingualText
  /** Rendered as a password input; the value flows into env/headers only. */
  readonly secret?: boolean
  readonly required?: boolean
  /** Prefilled value (ports, default hosts). */
  readonly initial?: string
  /** When present the field renders as a dropdown of these options. */
  readonly options?: readonly McpTemplateFieldOption[]
}

type McpTemplateIcon = 'database' | 'git' | 'cloud' | 'globe' | 'folder' | 'brain'

interface StdioBuild {
  readonly transport: 'stdio'
  readonly command: string
  readonly args: readonly string[]
  readonly env: Readonly<Record<string, string>>
}

interface HttpBuild {
  readonly transport: 'streamable-http'
  readonly url: string
  readonly headers: Readonly<Record<string, string>>
}

export type McpTemplateBuild = StdioBuild | HttpBuild

export interface McpTemplate {
  readonly id: string
  /** Human card title on the template shelf. */
  readonly name: BilingualText
  readonly serverName: string
  readonly icon: McpTemplateIcon
  readonly description: BilingualText
  readonly fields: readonly McpTemplateField[]
  readonly build: (values: Readonly<Record<string, string>>) => McpTemplateBuild
}

const YUNXIAO_MCP_URL =
  'https://openapi-rdc.aliyuncs.com/ai/mcp?toolsets=organization-management,project-management'

const DATABASE_TYPES: readonly McpTemplateFieldOption[] = [
  { value: 'postgresql', label: { en: 'PostgreSQL', zh: 'PostgreSQL' } },
  { value: 'mysql', label: { en: 'MySQL', zh: 'MySQL' } },
  { value: 'oracle', label: { en: 'Oracle', zh: 'Oracle' } },
]

const DEFAULT_DB_PORT: Readonly<Record<string, string>> = {
  postgresql: '5432',
  mysql: '3306',
  oracle: '1521',
}

const trimmed = (values: Readonly<Record<string, string>>, key: string): string =>
  (values[key] ?? '').trim()

/** Port with a per-database fallback so the field can stay empty. */
function databasePort(values: Readonly<Record<string, string>>): string {
  const explicit = trimmed(values, 'port')
  if (explicit.length > 0) return explicit
  return DEFAULT_DB_PORT[trimmed(values, 'dbtype')] ?? '5432'
}

function databaseBuild(values: Readonly<Record<string, string>>): McpTemplateBuild {
  const host = trimmed(values, 'host')
  const port = databasePort(values)
  const user = trimmed(values, 'user')
  const password = trimmed(values, 'password')
  const database = trimmed(values, 'database')
  const dbType = trimmed(values, 'dbtype')
  if (dbType === 'mysql') {
    return {
      transport: 'stdio',
      command: 'npx',
      args: ['-y', '@benborla29/mcp-server-mysql'],
      env: { MYSQL_HOST: host, MYSQL_PORT: port, MYSQL_USER: user, MYSQL_PASS: password, MYSQL_DB: database },
    }
  }
  if (dbType === 'oracle') {
    return {
      transport: 'stdio',
      command: 'uvx',
      args: ['mcp-server-oracle', '--user', user, '--password', password, '--dsn', `${host}:${port}/${database}`],
      env: {},
    }
  }
  return {
    transport: 'stdio',
    command: 'npx',
    args: [
      '-y',
      '@modelcontextprotocol/server-postgres',
      `postgresql://${user}:${password}@${host}:${port}/${database}`,
    ],
    env: {},
  }
}

/** The template shelf, ordered by expected usage. */
export const MCP_TEMPLATES: readonly McpTemplate[] = [
  {
    id: 'database',
    name: { en: 'Database', zh: '数据库' },
    serverName: 'database',
    icon: 'database',
    description: {
      en: 'Pick a database type and fill host, port, account, and password.',
      zh: '选择数据库类型，填地址、端口、账号、密码即可连接。',
    },
    fields: [
      { key: 'dbtype', label: { en: 'Database type', zh: '数据库类型' }, options: DATABASE_TYPES, initial: 'postgresql', required: true },
      { key: 'host', label: { en: 'Host', zh: '地址' }, placeholder: { en: '192.168.1.10', zh: '192.168.1.10' }, required: true },
      { key: 'port', label: { en: 'Port (optional)', zh: '端口（可不填）' } },
      { key: 'user', label: { en: 'Username', zh: '用户名' }, required: true },
      { key: 'password', label: { en: 'Password', zh: '密码' }, secret: true, required: true },
      { key: 'database', label: { en: 'Database / service name', zh: '数据库名 / 服务名' }, required: true },
    ],
    build: databaseBuild,
  },
  {
    id: 'gitlab',
    name: { en: 'GitLab', zh: 'GitLab' },
    serverName: 'gitlab',
    icon: 'git',
    description: {
      en: 'Browse issues, merge requests, and code on any GitLab instance.',
      zh: '连接任意 GitLab 实例，查 issue、合并请求和代码。',
    },
    fields: [
      { key: 'host', label: { en: 'GitLab URL', zh: 'GitLab 地址' }, initial: 'https://gitlab.com', required: true },
      { key: 'token', label: { en: 'Personal access token', zh: '个人访问令牌' }, secret: true, required: true },
    ],
    build: values => ({
      transport: 'stdio',
      command: 'npx',
      args: ['-y', '@zereight/mcp-gitlab'],
      env: {
        GITLAB_PERSONAL_ACCESS_TOKEN: trimmed(values, 'token'),
        GITLAB_API_URL: `${trimmed(values, 'host').replace(/\/+$/u, '')}/api/v4`,
      },
    }),
  },
  {
    id: 'yunxiao',
    name: { en: 'Yunxiao', zh: '云效' },
    serverName: 'yunxiao',
    icon: 'cloud',
    description: {
      en: 'Aliyun Yunxiao work items: query, comment, and update with your token.',
      zh: '阿里云云效工作项：用个人令牌查询、评论、更新需求。',
    },
    fields: [
      { key: 'token', label: { en: 'Yunxiao access token', zh: '云效个人访问令牌' }, secret: true, required: true },
    ],
    build: values => ({
      transport: 'streamable-http',
      url: YUNXIAO_MCP_URL,
      headers: { Authorization: `Bearer ${trimmed(values, 'token')}` },
    }),
  },
  {
    id: 'playwright',
    name: { en: 'Browser', zh: '浏览器' },
    serverName: 'browser',
    icon: 'globe',
    description: {
      en: 'Drive a real browser: open pages, click, screenshot, and save content.',
      zh: '驱动真实浏览器：打开网页、点击、截图、保存内容。',
    },
    fields: [],
    build: () => ({
      transport: 'stdio',
      command: 'npx',
      args: ['-y', '@playwright/mcp@latest'],
      env: {},
    }),
  },
  {
    id: 'filesystem',
    name: { en: 'Files', zh: '文件' },
    serverName: 'files',
    icon: 'folder',
    description: {
      en: 'Let the model read and write files inside one allowed folder.',
      zh: '让模型读写一个指定目录内的文件。',
    },
    fields: [
      {
        key: 'dir',
        label: { en: 'Allowed folder', zh: '允许访问的目录' },
        placeholder: { en: '/Users/you/Documents', zh: '/Users/你/Documents' },
        required: true,
      },
    ],
    build: values => ({
      transport: 'stdio',
      command: 'npx',
      args: ['-y', '@modelcontextprotocol/server-filesystem', trimmed(values, 'dir')],
      env: {},
    }),
  },
  {
    id: 'memory',
    name: { en: 'Memory', zh: '记忆' },
    serverName: 'memory',
    icon: 'brain',
    description: {
      en: 'A local knowledge graph the model can remember facts across sessions.',
      zh: '本地知识库，模型可以跨会话记住事实。',
    },
    fields: [],
    build: () => ({
      transport: 'stdio',
      command: 'npx',
      args: ['-y', '@modelcontextprotocol/server-memory'],
      env: {},
    }),
  },
  {
    id: 'http-generic',
    name: { en: 'Remote MCP', zh: '远程 MCP' },
    serverName: 'remote-mcp',
    icon: 'globe',
    description: {
      en: 'Connect any remote MCP endpoint with one URL and one header.',
      zh: '连接任意远程 MCP 服务：填一个地址和一个请求头。',
    },
    fields: [
      { key: 'url', label: { en: 'MCP URL', zh: 'MCP 地址' }, placeholder: { en: 'https://host/mcp', zh: 'https://host/mcp' }, required: true },
      { key: 'headerName', label: { en: 'Header name', zh: '请求头名称' }, initial: 'Authorization' },
      { key: 'headerValue', label: { en: 'Header value', zh: '请求头值' }, secret: true },
    ],
    build: values => {
      const name = trimmed(values, 'headerName')
      const value = trimmed(values, 'headerValue')
      return {
        transport: 'streamable-http',
        url: trimmed(values, 'url'),
        headers: name.length > 0 && value.length > 0 ? { [name]: value } : {},
      }
    },
  },
]

/** Look up one template by id. */
export function templateById(id: string): McpTemplate | undefined {
  return MCP_TEMPLATES.find(template => template.id === id)
}

/** Initial friendly-form values from field defaults. */
export function templateInitialValues(template: McpTemplate): Record<string, string> {
  const values: Record<string, string> = {}
  for (const field of template.fields) {
    values[field.key] = field.initial ?? ''
  }
  return values
}

/** True when every required field has a non-empty value. */
export function templateRequirementsMet(
  template: McpTemplate,
  values: Readonly<Record<string, string>>,
): boolean {
  return template.fields.every(field => field.required !== true || trimmed(values, field.key).length > 0)
}

/** Pick the inline template language from the renderer environment. */
export function templateText(text: BilingualText): string {
  if (typeof navigator === 'undefined') return text.zh
  return navigator.language.toLowerCase().startsWith('zh') ? text.zh : text.en
}
