import type { PropsLocale, PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'

export type CapabilityKey =
  | 'skillsNav' | 'skillsTitle' | 'skillsIntro' | 'skillsLoading' | 'skillsEmpty' | 'skillsUnavailable'
  | 'refresh' | 'userInvocable' | 'modelInvocable' | 'whenToUse' | 'privateSource' | 'projectSource' | 'bundledSource'
  | 'mcpNav' | 'mcpTitle' | 'mcpIntro' | 'mcpLoading' | 'mcpEmpty' | 'addServer' | 'templates' | 'importJson' | 'exportAll'
  | 'importHint' | 'save' | 'saving' | 'savedRestart' | 'saveFailed' | 'test' | 'testing' | 'testOk' | 'testFailed'
  | 'enabled' | 'disabled' | 'delete' | 'edit' | 'done' | 'exportJson' | 'serverName' | 'transport' | 'stdio' | 'http'
  | 'command' | 'arguments' | 'environment' | 'workingDirectory' | 'url' | 'headers' | 'secretHint' | 'invalidJson'
  | 'duplicateName' | 'customServer' | 'toolCount' | 'connectionUnknown' | 'pendingRestart' | 'redactedExport'
  | 'configure' | 'addNow' | 'cancel' | 'requiredFields' | 'databaseType' | 'status' | 'advancedSettings'

export const CAPABILITY_NS = 'overlay.capabilities'

export const capabilityZh: Record<CapabilityKey, string> = {
  skillsNav: '技能', skillsTitle: '技能', skillsIntro: '当前官网桌面组合已经发现并可供会话调用的技能。输入“/”也可以直接选择技能。',
  skillsLoading: '正在读取技能目录…', skillsEmpty: '尚未发现技能。可以在用户或项目 Skills 目录中添加技能。', skillsUnavailable: '当前组合未挂载技能目录。',
  refresh: '刷新', userInvocable: '用户可调用', modelInvocable: '模型可调用', whenToUse: '适用场景', privateSource: '本机私有', projectSource: '项目公用', bundledSource: '内置',
  mcpNav: 'MCP', mcpTitle: 'MCP 服务器', mcpIntro: '每个 MCP 都是一张独立卡片，可以测试连接、编辑、启停、删除和导出配置。',
  mcpLoading: '正在读取 MCP 配置…', mcpEmpty: '尚未配置 MCP。可以从常用模板开始，或者粘贴现成 JSON。', addServer: '自定义 MCP', templates: '常用 MCP',
  importJson: '导入 JSON', exportAll: '导出全部', importHint: '支持 Claude Desktop 的 {"mcpServers": {...}}、单个服务器或服务器数组。',
  save: '保存全部', saving: '保存中…', savedRestart: '已保存，重启官网版 DSH 后生效。', saveFailed: '保存失败，请检查配置。',
  test: '测试连接', testing: '测试中…', testOk: '连接成功', testFailed: '连接失败', enabled: '已启用', disabled: '已停用', delete: '删除', edit: '编辑', done: '完成', exportJson: '导出 JSON',
  serverName: '名称', transport: '连接方式', stdio: '本地命令', http: '远程 MCP', command: '启动命令', arguments: '参数（每行一个）', environment: '环境变量（KEY=value，每行一个）',
  workingDirectory: '工作目录（可选）', url: 'MCP 地址', headers: '请求头（KEY=value，每行一个）', secretHint: '已保存的敏感值不会回显；留空会保留原值。',
  invalidJson: '无法识别这段 MCP JSON。', duplicateName: 'MCP 名称不能重复。', customServer: '自定义 MCP', toolCount: '个工具', connectionUnknown: '尚未测试', pendingRestart: '等待重启生效',
  redactedExport: '为保护账号，导出的 JSON 不包含已保存的 Token、密码和请求头值。', configure: '配置', addNow: '添加', cancel: '取消', requiredFields: '请填写所有必填项。', databaseType: '数据库类型',
  status: '状态', advancedSettings: '高级设置',
}

export const capabilityEn: Record<CapabilityKey, string> = {
  skillsNav: 'Skills', skillsTitle: 'Skills', skillsIntro: 'Skills discovered by the official Desktop composition and available to sessions. Type “/” to invoke one directly.',
  skillsLoading: 'Loading skills…', skillsEmpty: 'No skills discovered. Add one to a user or project Skills directory.', skillsUnavailable: 'The active composition has no skill registry.',
  refresh: 'Refresh', userInvocable: 'User invocable', modelInvocable: 'Model invocable', whenToUse: 'When to use', privateSource: 'Private', projectSource: 'Project', bundledSource: 'Bundled',
  mcpNav: 'MCP', mcpTitle: 'MCP servers', mcpIntro: 'Every MCP is an independent card with test, edit, enable, delete, and JSON export actions.',
  mcpLoading: 'Loading MCP configuration…', mcpEmpty: 'No MCP configured. Start from a common template or paste existing JSON.', addServer: 'Custom MCP', templates: 'Common MCPs',
  importJson: 'Import JSON', exportAll: 'Export all', importHint: 'Accepts Claude Desktop {"mcpServers": {...}}, one server, or a server array.',
  save: 'Save all', saving: 'Saving…', savedRestart: 'Saved. Restart official DSH to apply.', saveFailed: 'Save failed. Check the configuration.',
  test: 'Test connection', testing: 'Testing…', testOk: 'Connected', testFailed: 'Connection failed', enabled: 'Enabled', disabled: 'Disabled', delete: 'Delete', edit: 'Edit', done: 'Done', exportJson: 'Export JSON',
  serverName: 'Name', transport: 'Transport', stdio: 'Local command', http: 'Remote MCP', command: 'Command', arguments: 'Arguments (one per line)', environment: 'Environment (KEY=value, one per line)',
  workingDirectory: 'Working directory (optional)', url: 'MCP URL', headers: 'Headers (KEY=value, one per line)', secretHint: 'Stored secrets are not returned. Leave blank to keep them.',
  invalidJson: 'This MCP JSON could not be imported.', duplicateName: 'MCP names must be unique.', customServer: 'Custom MCP', toolCount: 'tools', connectionUnknown: 'Not tested', pendingRestart: 'Restart required',
  redactedExport: 'For account safety, exported JSON omits stored tokens, passwords, and header values.', configure: 'Configure', addNow: 'Add', cancel: 'Cancel', requiredFields: 'Fill every required field.', databaseType: 'Database type',
  status: 'Status', advancedSettings: 'Advanced settings',
}

declare module '@deepseek-ai/dsh-client-ui-slots' {
  interface LocaleNamespaceMap { 'overlay.capabilities': CapabilityKey }
}

export type CapabilitySectionProps = PropsRuntime<'settings.section'> & PropsLocale<'overlay.capabilities'>
