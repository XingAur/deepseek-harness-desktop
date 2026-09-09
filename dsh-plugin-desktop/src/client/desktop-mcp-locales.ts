/** Copy for the Desktop MCP settings page, keyed by {@link DesktopMcpLocaleKey}. */

/** Locale keys owned by the Desktop MCP settings page. */
export type DesktopMcpLocaleKey =
  | 'nav'
  | 'title'
  | 'description'
  | 'loading'
  | 'unavailable'
  | 'retry'
  | 'saveFailed'
  | 'empty'
  | 'addServer'
  | 'serverName'
  | 'transport'
  | 'transportStdio'
  | 'transportHttp'
  | 'command'
  | 'args'
  | 'argsHint'
  | 'cwd'
  | 'url'
  | 'env'
  | 'envHint'
  | 'headers'
  | 'headersHint'
  | 'addValue'
  | 'removeValue'
  | 'enabled'
  | 'disabled'
  | 'enable'
  | 'disable'
  | 'edit'
  | 'delete'
  | 'deleteConfirm'
  | 'cancel'
  | 'save'
  | 'restartRequired'
  | 'import'
  | 'importHint'
  | 'importApply'
  | 'importInvalid'
  | 'importDuplicate'
  | 'templateTitle'
  | 'test'
  | 'testing'
  | 'testOk'
  | 'testToolsUnit'
  | 'testFailed'
  | 'nameOptionalHint'
  | 'templateBlank'
  | 'templateAdvancedNote'
  | 'nameRequired'
  | 'commandRequired'
  | 'urlRequired'
  | 'duplicateName'

/** English copy. */
export const en: Record<DesktopMcpLocaleKey, string> = {
  nav: 'MCP',
  title: 'MCP servers',
  description: 'Tools from external MCP servers appear as mcp__<name>__<tool>. Changes apply after the app restarts.',
  loading: 'Loading MCP servers…',
  unavailable: 'The MCP server state could not be read. Please try again.',
  retry: 'Retry',
  saveFailed: 'The MCP servers could not be saved. Please try again.',
  empty: 'No MCP servers configured.',
  addServer: 'Add server',
  serverName: 'Server name',
  transport: 'Transport',
  transportStdio: 'Stdio command',
  transportHttp: 'Streamable HTTP',
  command: 'Command',
  args: 'Arguments',
  argsHint: 'One argument per line.',
  cwd: 'Working directory',
  url: 'URL',
  env: 'Environment variables',
  envHint: 'Values are saved but never displayed again. Submit an existing name to replace its value.',
  headers: 'Headers',
  headersHint: 'Values are saved but never displayed again. Submit an existing name to replace its value.',
  addValue: 'Add',
  removeValue: 'Remove stored value',
  enabled: 'Enabled',
  disabled: 'Disabled',
  enable: 'Enable',
  disable: 'Disable',
  edit: 'Edit',
  delete: 'Delete',
  deleteConfirm: 'Delete this MCP server? Its tools disappear after the next restart.',
  cancel: 'Cancel',
  save: 'Save',
  restartRequired: 'Restart the app to apply MCP changes.',
  import: 'Paste JSON',
  importHint: 'Accepts Claude Desktop {"mcpServers":{...}} or a plain array.',
  importApply: 'Import',
  importInvalid: 'The pasted JSON is not a valid MCP server list.',
  importDuplicate: 'Servers with existing names were skipped.',
  templateTitle: 'Add from a template',
  test: 'Test connection',
  testing: 'Testing…',
  testOk: 'Connected',
  testToolsUnit: 'tools',
  testFailed: 'Connection failed',
  nameOptionalHint: 'Leave empty to auto-name.',
  templateBlank: 'Blank (advanced)',
  templateAdvancedNote: 'Connection details are assembled automatically; switch to a blank server to edit raw command and args.',
  nameRequired: 'Server name is required.',
  commandRequired: 'A command is required for stdio servers.',
  urlRequired: 'A URL is required for Streamable HTTP servers.',
  duplicateName: 'A server with this name already exists.',
}

/** Simplified Chinese copy. */
export const zh: Record<DesktopMcpLocaleKey, string> = {
  nav: 'MCP',
  title: 'MCP 服务器',
  description: '来自外部 MCP 服务器的工具以 mcp__<名称>__<工具> 的形式出现。更改将在应用重启后生效。',
  loading: '正在加载 MCP 服务器…',
  unavailable: 'MCP 服务器状态读取失败，请重试。',
  retry: '重试',
  saveFailed: 'MCP 服务器保存失败，请重试。',
  empty: '尚未配置 MCP 服务器。',
  addServer: '添加服务器',
  serverName: '服务器名称',
  transport: '传输方式',
  transportStdio: 'Stdio 命令',
  transportHttp: 'Streamable HTTP',
  command: '命令',
  args: '参数',
  argsHint: '每行一个参数。',
  cwd: '工作目录',
  url: 'URL',
  env: '环境变量',
  envHint: '值保存后不再回显。提交同名条目可替换已保存的值。',
  headers: '请求头',
  headersHint: '值保存后不再回显。提交同名条目可替换已保存的值。',
  addValue: '添加',
  removeValue: '移除已保存的值',
  enabled: '已启用',
  disabled: '已禁用',
  enable: '启用',
  disable: '禁用',
  edit: '编辑',
  delete: '删除',
  deleteConfirm: '确定删除该 MCP 服务器？其工具将在下次重启后消失。',
  cancel: '取消',
  save: '保存',
  restartRequired: '重启应用以应用 MCP 更改。',
  import: '粘贴 JSON',
  importHint: '支持 Claude Desktop 的 {"mcpServers":{...}} 或数组。',
  importApply: '导入',
  importInvalid: '粘贴的 JSON 不是有效的 MCP 服务器列表。',
  importDuplicate: '与现有服务器同名的条目已跳过。',
  templateTitle: '从模板添加',
  test: '测试连接',
  testing: '测试中…',
  testOk: '连接成功',
  testToolsUnit: '个工具',
  testFailed: '连接失败',
  nameOptionalHint: '留空自动命名。',
  templateBlank: '空白自定义（高级）',
  templateAdvancedNote: '连接参数会自动拼装；如需直接编辑命令和参数，请用空白自定义。',
  nameRequired: '请填写服务器名称。',
  commandRequired: 'Stdio 服务器需要填写命令。',
  urlRequired: 'Streamable HTTP 服务器需要填写 URL。',
  duplicateName: '已存在同名服务器。',
}
