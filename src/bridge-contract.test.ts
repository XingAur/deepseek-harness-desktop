import { describe, expect, it } from 'vitest'
import {
  DESKTOP_BRIDGE_V2_CHANNEL,
  bridgeCommandByActionV2,
  isVersionedBridgePayload,
  isVersionedBridgeRequest,
  isVersionedBridgeResponse,
} from './bridge-contract'

const request = {
  channel: DESKTOP_BRIDGE_V2_CHANNEL,
  requestId: 'request-1',
  generationId: 'generation-1',
  sessionId: 'session-1',
  action: 'task.create',
  payload: { workspaceId: 'workspace-1', prompt: '检查项目', permission: 'request-approval' },
}

describe('dsh-desktop/v2 bridge contract', () => {
  it('accepts a strict versioned task request and maps it to a named command', () => {
    expect(isVersionedBridgeRequest(request)).toBe(true)
    expect(bridgeCommandByActionV2['task.create']).toBe('agent_task_create')
  })

  it('accepts only paired, identifier-safe provider and agent selections', () => {
    expect(isVersionedBridgeRequest({
      ...request,
      payload: {
        ...request.payload,
        providerId: 'claude',
        agentId: 'claude:default',
      },
    })).toBe(true)
    expect(isVersionedBridgeRequest({
      ...request,
      payload: { ...request.payload, providerId: 'claude' },
    })).toBe(false)
    expect(isVersionedBridgeRequest({
      ...request,
      payload: { ...request.payload, agentId: 'claude:../escape', providerId: 'claude' },
    })).toBe(false)
  })

  it('accepts an empty task-list query without widening task mutation payloads', () => {
    expect(isVersionedBridgePayload('task.list', {})).toBe(true)
    expect(isVersionedBridgeRequest({ ...request, action: 'task.list', payload: {} })).toBe(true)
    expect(isVersionedBridgeRequest({ ...request, action: 'task.list', payload: { taskId: 'task-1' } })).toBe(false)
    expect(bridgeCommandByActionV2['task.list']).toBe('agent_task_list')
  })

it('maps the codex cli lifecycle actions to dedicated commands with provider payloads', () => {
    expect(bridgeCommandByActionV2['cli.install.status']).toBe('agent_cli_install_status')
    expect(bridgeCommandByActionV2['cli.install.start']).toBe('agent_cli_install_start')
    expect(bridgeCommandByActionV2['cli.login.status']).toBe('agent_cli_login_status')
    expect(bridgeCommandByActionV2['cli.login.start']).toBe('agent_cli_login_start')
    for (const action of ['cli.install.status', 'cli.install.start', 'cli.login.status', 'cli.login.start'] as const) {
      expect(isVersionedBridgePayload(action, { providerId: 'codex' })).toBe(true)
      expect(isVersionedBridgePayload(action, { providerId: '../escape' })).toBe(false)
      expect(isVersionedBridgePayload(action, { providerId: 'codex', command: ['npm'] })).toBe(false)
      expect(isVersionedBridgeRequest({ ...request, action, payload: { providerId: 'codex' } })).toBe(true)
    }
  })

it('maps plugin market actions to dedicated commands with bounded payloads', () => {
    expect(bridgeCommandByActionV2['plugin.catalog.list']).toBe('agent_plugin_catalog')
    expect(bridgeCommandByActionV2['plugin.install.start']).toBe('agent_plugin_install_start')
    expect(bridgeCommandByActionV2['plugin.install.status']).toBe('agent_plugin_install_status')
    expect(isVersionedBridgePayload('plugin.catalog.list', {})).toBe(true)
    expect(isVersionedBridgePayload('plugin.catalog.list', { query: '余额', category: 'tools', offset: 0, limit: 30 })).toBe(true)
    expect(isVersionedBridgePayload('plugin.catalog.list', { query: 'x'.repeat(121) })).toBe(false)
    expect(isVersionedBridgePayload('plugin.catalog.list', { limit: 51 })).toBe(false)
    expect(isVersionedBridgePayload('plugin.catalog.list', { pluginId: 'a/b' })).toBe(false)
    expect(isVersionedBridgePayload('plugin.install.start', { pluginId: 'owner/repo' })).toBe(true)
    expect(isVersionedBridgePayload('plugin.install.start', { pluginId: '../escape' })).toBe(false)
    expect(isVersionedBridgePayload('plugin.install.status', { pluginId: 'a/b' })).toBe(true)
  })

  it('maps local skill creation and native-folder import with bounded instructions', () => {
    expect(bridgeCommandByActionV2['skill.create']).toBe('agent_skill_create')
    expect(bridgeCommandByActionV2['skill.import']).toBe('agent_skill_import')
    expect(isVersionedBridgePayload('skill.import', {})).toBe(true)
    expect(isVersionedBridgePayload('skill.import', { path: '/tmp/untrusted' })).toBe(false)
    expect(isVersionedBridgePayload('skill.create', {
      skillId: 'code-review',
      displayName: '代码审查',
      description: '执行本地代码审查',
      instructions: '先读取目标文件，再按严重程度输出问题。',
    })).toBe(true)
    expect(isVersionedBridgePayload('skill.create', {
      skillId: '../escape', displayName: 'bad', description: '', instructions: 'bad',
    })).toBe(false)
    expect(isVersionedBridgePayload('skill.create', {
      skillId: 'oversized', displayName: 'oversized', description: '', instructions: 'x'.repeat(16 * 1024 + 1),
    })).toBe(false)
  })

  it('requires a bounded non-empty task prompt', () => {
    expect(isVersionedBridgePayload('task.create', {
      workspaceId: 'workspace-1',
      prompt: '检查项目',
      permission: 'request-approval',
    })).toBe(true)
    expect(isVersionedBridgePayload('task.create', {
      workspaceId: 'workspace-1',
      prompt: '   ',
      permission: 'request-approval',
    })).toBe(false)
    expect(isVersionedBridgePayload('task.create', {
      workspaceId: 'workspace-1',
      prompt: 'x'.repeat(16 * 1024 + 1),
      permission: 'request-approval',
    })).toBe(false)
  })

  it('rejects unknown actions, fields, malformed ids, and oversized payloads', () => {
    expect(isVersionedBridgeRequest({ ...request, action: 'shell.execute' })).toBe(false)
    expect(isVersionedBridgeRequest({ ...request, unexpected: true })).toBe(false)
    expect(isVersionedBridgeRequest({ ...request, payload: { workspaceId: 'workspace-1', prompt: '检查项目', permission: 'request-approval', shell: 'rm -rf' } })).toBe(false)
    expect(isVersionedBridgeRequest({ ...request, generationId: '../escape' })).toBe(false)
    expect(isVersionedBridgeRequest({
      ...request,
      payload: { output: 'x'.repeat(33 * 1024) },
    })).toBe(false)
  })

  it('keeps content-reference reads within the host-side 16 KiB bound', () => {
    const contentRequest = {
      ...request,
      action: 'content-reference.read',
      payload: { contentRefId: 'ref-1', taskId: 'task-1', offset: 0, length: 16 * 1024 },
    }
    expect(isVersionedBridgeRequest(contentRequest)).toBe(true)
    expect(isVersionedBridgeRequest({
      ...contentRequest,
      payload: { ...contentRequest.payload, length: 16 * 1024 + 1 },
    })).toBe(false)
  })

  it('rejects secret-shaped results before they reach the iframe', () => {
    expect(isVersionedBridgeResponse({
      channel: DESKTOP_BRIDGE_V2_CHANNEL,
      requestId: request.requestId,
      generationId: request.generationId,
      sessionId: request.sessionId,
      ok: true,
      result: { secret: 'must-not-cross' },
    })).toBe(false)
  })

  it('allows a credential to be explicitly bound to a provider without accepting unsafe ids', () => {
    expect(isVersionedBridgePayload('credential.put', { providerId: 'codex', secret: 'private-value' })).toBe(true)
    expect(isVersionedBridgePayload('credential.put', { providerId: '../escape', secret: 'private-value' })).toBe(false)
  })

  it('routes Harness lifecycle actions and requires its generated task evidence paths', () => {
    expect(bridgeCommandByActionV2['harness.status']).toBe('harness_status')
    expect(bridgeCommandByActionV2['harness.cancel']).toBe('harness_cancel')
    expect(isVersionedBridgePayload('harness.status', {})).toBe(true)
    expect(isVersionedBridgePayload('harness.start', {
      taskContractPath: '/tmp/task-contract.json',
      understandingPath: '/tmp/understanding.json',
      worktreeRoot: '/tmp/project',
      knowledgeHome: '/tmp/knowledge',
      authorizationId: 'DFHIS-32178-change-1',
      agentBackend: 'host-bridge',
    })).toBe(true)
    expect(isVersionedBridgePayload('harness.start', {
      taskContractPath: '/tmp/task-contract.json',
      understandingPath: '/tmp/understanding.json',
      worktreeRoot: '/tmp/project',
      knowledgeHome: '/tmp/knowledge',
      authorizationId: 'DFHIS-32178-change-1',
      unexpected: true,
    })).toBe(false)
  })

  it('routes maintainable MCP and database profiles without allowing secrets in profile payloads', () => {
    expect(bridgeCommandByActionV2['harness.connection.list']).toBe('harness_connection_list')
    expect(bridgeCommandByActionV2['harness.connection.save']).toBe('harness_connection_save')
    expect(isVersionedBridgePayload('harness.connection.list', { kind: 'database' })).toBe(true)
    expect(isVersionedBridgePayload('harness.connection.save', {
      profileId: 'his-db-readonly', kind: 'database', providerId: 'generic', displayName: 'HIS 只读库',
      endpoint: 'postgresql://db.internal:5432/his', readOnly: true, enabled: true,
      transport: 'database', databaseType: 'postgresql', host: 'db.internal', port: 5432,
      databaseName: 'his', username: 'readonly_user', encoding: 'UTF-8', testQuery: 'SELECT 1',
      credentialId: 'credential-1',
    })).toBe(true)
    expect(isVersionedBridgePayload('harness.connection.save', {
      kind: 'database', displayName: 'HIS 只读库', password: 'must-not-cross',
    })).toBe(false)
  })

  it('accepts generalized custom connection profiles with bounded transport fields', () => {
    expect(isVersionedBridgePayload('harness.connection.list', { kind: 'http-api' })).toBe(true)
    expect(isVersionedBridgePayload('harness.connection.save', {
      profileId: 'project-memory',
      kind: 'mcp',
      transport: 'stdio',
      templateId: 'custom',
      displayName: '项目记忆',
      command: 'node',
      args: ['server.js', '--readonly'],
      environmentKeys: ['PROJECT_ROOT'],
      workingDirectoryPolicy: 'workspace',
      endpoint: '',
      healthPath: '',
      readOnly: true,
      enabled: true,
    })).toBe(true)
    expect(isVersionedBridgePayload('harness.connection.save', {
      kind: 'http-api',
      transport: 'http',
      templateId: 'custom',
      providerId: 'internal-search',
      displayName: '内部搜索 API',
      endpoint: 'https://search.example.test',
      healthPath: '/health',
      command: '',
      args: [],
      environmentKeys: [],
      workingDirectoryPolicy: 'none',
      readOnly: true,
      enabled: true,
    })).toBe(true)
  })

  it('rejects secret-bearing or unsafe generalized connection payloads', () => {
    expect(isVersionedBridgePayload('harness.connection.save', {
      kind: 'http-api', transport: 'http', templateId: 'custom', displayName: 'Unsafe API',
      endpoint: 'https://user:password@example.test', healthPath: '/health', command: '', args: [],
      environmentKeys: [], workingDirectoryPolicy: 'none', readOnly: true, enabled: true,
    })).toBe(false)
    expect(isVersionedBridgePayload('harness.connection.save', {
      kind: 'mcp', transport: 'stdio', templateId: 'custom', displayName: 'Unsafe MCP',
      endpoint: '', command: 'node', args: [], environmentKeys: ['API_TOKEN=secret'],
      workingDirectoryPolicy: 'workspace', readOnly: true, enabled: true,
    })).toBe(false)
    expect(isVersionedBridgePayload('harness.connection.save', {
      kind: 'http-api', transport: 'http', templateId: 'custom', displayName: 'Secret API',
      endpoint: 'https://example.test', healthPath: '/health', command: '', args: [], environmentKeys: [],
      workingDirectoryPolicy: 'none', readOnly: true, enabled: true, authorization: 'Bearer secret',
    })).toBe(false)
  })

  it('accepts a selected Harness package without requiring generated file paths', () => {
    expect(isVersionedBridgePayload('harness.start', {
      archiveRoot: '/Users/test/harness/DFHIS-32178/harness',
      worktreeRoot: '/Users/test/project',
      knowledgeHome: '/Users/test/knowledge',
      authorizationId: 'DFHIS-32178-change-1',
      selectedModelId: 'gpt-5.6-sol',
    })).toBe(true)
  })

  it('accepts a read-only Yunxiao intake source without allowing credentials in the source', () => {
    expect(bridgeCommandByActionV2['harness.intake']).toBe('harness_intake')
    expect(bridgeCommandByActionV2['harness.chat.start']).toBe('harness_chat_start')
    expect(bridgeCommandByActionV2['harness.pick-evidence-files']).toBe('harness_pick_evidence_files')
    expect(isVersionedBridgePayload('harness.intake', {
      source: 'https://devops.aliyun.com/projex/req/DFHIS-39999',
      archiveRoot: '/Users/test/harness-archives',
      includeComments: true,
    })).toBe(true)
    expect(isVersionedBridgePayload('harness.intake', {
      source: 'DFHIS-39999',
      archiveRoot: '/Users/test/harness-archives',
    })).toBe(true)
    expect(isVersionedBridgePayload('harness.intake', {
      source: 'https://devops.aliyun.com/projex/req/DFHIS-39999?token=secret',
      archiveRoot: '/Users/test/harness-archives',
    })).toBe(false)
  })

  it('validates the main-chat source and evidence payload without exposing internal task paths', () => {
    expect(isVersionedBridgePayload('harness.chat.start', {
      prompt: '完成需求',
      yunxiaoSource: 'DFHIS-12345',
      evidencePaths: ['/tmp/需求.png'],
    })).toBe(true)
    expect(isVersionedBridgePayload('harness.chat.start', { prompt: '完成需求', yunxiaoSource: 'not-a-work-item' })).toBe(false)
    expect(isVersionedBridgePayload('harness.pick-evidence-files', {})).toBe(true)
  })

  it('carries any provider-defined selected model into the intake and rejects malformed backend ids', () => {
    expect(isVersionedBridgePayload('harness.intake', {
      source: 'DFHIS-39999',
      archiveRoot: '/Users/test/harness-archives',
      selectedModelId: 'deepseek-reasoner',
      agentBackend: 'deepseek',
    })).toBe(true)
    expect(isVersionedBridgePayload('harness.intake', {
      source: 'DFHIS-39999',
      archiveRoot: '/Users/test/harness-archives',
      agentBackend: 'HOST-BRIDGE',
    })).toBe(false)
    expect(isVersionedBridgePayload('harness.intake', {
      source: 'DFHIS-39999',
      archiveRoot: '/Users/test/harness-archives',
      selectedModelId: 'openrouter/qwen/qwen3-235b-a22b:free',
    })).toBe(true)
    expect(isVersionedBridgePayload('harness.intake', {
      source: 'DFHIS-39999',
      archiveRoot: '/Users/test/harness-archives',
      selectedModelId: 'model\u0000id',
    })).toBe(false)
  })

  it('exposes the native archive-root picker without any payload fields', () => {
    expect(bridgeCommandByActionV2['harness.pick-archive-root']).toBe('harness_pick_archive_root')
    expect(isVersionedBridgePayload('harness.pick-archive-root', {})).toBe(true)
    expect(isVersionedBridgePayload('harness.pick-archive-root', { path: '/tmp' })).toBe(false)
  })

  it('carries bounded business answers into the task package', () => {
    expect(bridgeCommandByActionV2['harness.archive-answers']).toBe('harness_archive_answers')
    expect(isVersionedBridgePayload('harness.archive-answers', {
      archiveRoot: '/Users/test/harness-archives/DFHIS-39999/harness',
      answers: '重打记录按操作员过滤。',
    })).toBe(true)
    expect(isVersionedBridgePayload('harness.archive-answers', {
      archiveRoot: '/Users/test/harness-archives/DFHIS-39999/harness',
      answers: 'x'.repeat(8001),
    })).toBe(false)
    expect(isVersionedBridgePayload('harness.archive-answers', { answers: '有效答复' })).toBe(false)
  })
})

describe('prompts v2 actions', () => {
  it('accepts prompts.save with bounded title and content', () => {
    expect(isVersionedBridgePayload('prompts.save', { presetId: undefined, title: '标题', content: '正文' })).toBe(true)
    expect(isVersionedBridgePayload('prompts.save', { title: '标题', content: '正文' })).toBe(true)
    expect(isVersionedBridgePayload('prompts.save', { presetId: 'p1', title: '标题', content: 'x'.repeat(24 * 1024) })).toBe(true)
    expect(isVersionedBridgePayload('prompts.save', { title: '标题', content: 'x'.repeat(24 * 1024 + 1) })).toBe(false)
    expect(isVersionedBridgePayload('prompts.save', { title: '', content: '正文' })).toBe(false)
    expect(isVersionedBridgePayload('prompts.save', { title: 'x'.repeat(201), content: '正文' })).toBe(false)
    expect(isVersionedBridgePayload('prompts.save', { title: '标题', content: '正文', extra: 1 })).toBe(false)
  })

  it('accepts prompts.resolve-conflict with the same bounds as save', () => {
    expect(isVersionedBridgePayload('prompts.resolve-conflict', { presetId: 'p1', title: '标题', content: '正文' })).toBe(true)
    expect(isVersionedBridgePayload('prompts.resolve-conflict', { presetId: 'p1', title: '标题', content: 'x'.repeat(24 * 1024 + 1) })).toBe(false)
    expect(isVersionedBridgePayload('prompts.resolve-conflict', { title: '标题', content: '正文' })).toBe(false)
  })

  it('accepts prompts.activate/deactivate with known targets only', () => {
    expect(isVersionedBridgePayload('prompts.activate', { presetId: 'p1', target: 'claude' })).toBe(true)
    expect(isVersionedBridgePayload('prompts.activate', { presetId: 'p1', target: 'gemini' })).toBe(false)
    expect(isVersionedBridgePayload('prompts.deactivate', { target: 'dsh' })).toBe(true)
    expect(isVersionedBridgePayload('prompts.deactivate', {})).toBe(false)
  })

  it('accepts prompts.import with a deduplicated target list', () => {
    expect(isVersionedBridgePayload('prompts.import', { targets: ['claude', 'codex'] })).toBe(true)
    expect(isVersionedBridgePayload('prompts.import', { targets: ['claude', 'claude'] })).toBe(false)
    expect(isVersionedBridgePayload('prompts.import', { targets: [] })).toBe(false)
    expect(isVersionedBridgePayload('prompts.import', { targets: ['claude', 'codex'], extra: 1 })).toBe(false)
  })

  it('accepts prompts.get/delete/status/list payloads', () => {
    expect(isVersionedBridgePayload('prompts.get', { presetId: 'p1' })).toBe(true)
    expect(isVersionedBridgePayload('prompts.delete', { presetId: 'p1' })).toBe(true)
    expect(isVersionedBridgePayload('prompts.status', {})).toBe(true)
    expect(isVersionedBridgePayload('prompts.list', {})).toBe(true)
    expect(isVersionedBridgePayload('prompts.get', {})).toBe(false)
  })

  it('maps prompts actions to tauri commands', () => {
    expect(bridgeCommandByActionV2['prompts.list']).toBe('prompts_list')
    expect(bridgeCommandByActionV2['prompts.resolve-conflict']).toBe('prompts_resolve_conflict')
    expect(bridgeCommandByActionV2['prompts.import']).toBe('prompts_import')
  })
})

describe('mcp v2 actions', () => {
  const DEF = {
    name: 'fetch',
    command: 'npx',
    args: ['-y'],
    env: { NO_PROXY: '127.0.0.1' },
    targets: ['claude', 'codex'],
  }

  it('accepts mcp.upsert definitions with bounded fields', () => {
    expect(isVersionedBridgePayload('mcp.upsert', DEF)).toBe(true)
    expect(isVersionedBridgePayload('mcp.upsert', { ...DEF, id: 'srv-1' })).toBe(true)
    expect(isVersionedBridgePayload('mcp.upsert', { ...DEF, args: undefined, env: undefined })).toBe(true)
    expect(isVersionedBridgePayload('mcp.upsert', { ...DEF, targets: [] })).toBe(false)
    expect(isVersionedBridgePayload('mcp.upsert', { ...DEF, targets: ['claude', 'dsh'] })).toBe(false)
    expect(isVersionedBridgePayload('mcp.upsert', { ...DEF, targets: ['claude', 'claude'] })).toBe(false)
    expect(isVersionedBridgePayload('mcp.upsert', { ...DEF, name: '   ' })).toBe(false)
    expect(isVersionedBridgePayload('mcp.upsert', { ...DEF, name: 'x'.repeat(201) })).toBe(false)
    expect(isVersionedBridgePayload('mcp.upsert', { ...DEF, command: '' })).toBe(false)
    expect(isVersionedBridgePayload('mcp.upsert', { ...DEF, command: 'npx\r-del' })).toBe(false)
    expect(isVersionedBridgePayload('mcp.upsert', { ...DEF, env: { 'BAD KEY': '1' } })).toBe(false)
    const tooManyEnv = Object.fromEntries(Array.from({ length: 33 }, (_, index) => [`K${index}`, 'v']))
    expect(isVersionedBridgePayload('mcp.upsert', { ...DEF, env: tooManyEnv })).toBe(false)
    expect(isVersionedBridgePayload('mcp.upsert', { ...DEF, extra: 1 })).toBe(false)
  })

  it('accepts mcp.sync/import with known targets and delete with an id', () => {
    expect(isVersionedBridgePayload('mcp.sync', { target: 'claude' })).toBe(true)
    expect(isVersionedBridgePayload('mcp.sync', { target: 'codex' })).toBe(true)
    expect(isVersionedBridgePayload('mcp.sync', { target: 'dsh' })).toBe(false) // MCP MVP 不含 DSH 目标
    expect(isVersionedBridgePayload('mcp.sync', {})).toBe(false)
    expect(isVersionedBridgePayload('mcp.import', { target: 'codex' })).toBe(true)
    expect(isVersionedBridgePayload('mcp.delete', { id: 'srv-1' })).toBe(true)
    expect(isVersionedBridgePayload('mcp.delete', {})).toBe(false)
    expect(isVersionedBridgePayload('mcp.list', {})).toBe(true)
    expect(isVersionedBridgePayload('mcp.status', {})).toBe(true)
    expect(isVersionedBridgeRequest({ ...request, action: 'mcp.list', payload: {} })).toBe(true)
    expect(isVersionedBridgeRequest({ ...request, action: 'mcp.sync', payload: { target: 'claude' } })).toBe(true)
  })

  it('maps mcp actions to tauri commands', () => {
    expect(bridgeCommandByActionV2['mcp.list']).toBe('mcp_manager_list')
    expect(bridgeCommandByActionV2['mcp.upsert']).toBe('mcp_manager_upsert')
    expect(bridgeCommandByActionV2['mcp.delete']).toBe('mcp_manager_delete')
    expect(bridgeCommandByActionV2['mcp.sync']).toBe('mcp_manager_sync')
    expect(bridgeCommandByActionV2['mcp.import']).toBe('mcp_manager_import')
    expect(bridgeCommandByActionV2['mcp.status']).toBe('mcp_manager_status')
  })

  it('lets MCP definition results carry user-authored env but still rejects other secrets', () => {
    const response = {
      channel: DESKTOP_BRIDGE_V2_CHANNEL,
      requestId: 'request-1',
      generationId: 'generation-1',
      sessionId: 'session-1',
      ok: true,
    }
    const mcpResult = { ...response, result: [{ id: 'srv-1', name: 'fetch', command: 'npx', env: { API_KEY: 'user-entered' } }] }
    expect(isVersionedBridgeResponse(mcpResult, { allowSecretShapedResult: true })).toBe(true)
    expect(isVersionedBridgeResponse(mcpResult)).toBe(false)
    expect(isVersionedBridgeResponse({ ...response, result: { secret: 'must-not-cross' } }, { allowSecretShapedResult: true })).toBe(false)
  })
})

describe('skills v2 actions', () => {
  it('accepts skills.list with a known target only', () => {
    expect(isVersionedBridgePayload('skills.list', { target: 'claude' })).toBe(true)
    expect(isVersionedBridgePayload('skills.list', { target: 'dsh' })).toBe(false) // Skills MVP 不含 DSH 目标
    expect(isVersionedBridgePayload('skills.list', {})).toBe(false)
    expect(isVersionedBridgePayload('skills.list', { target: 'claude', extra: 1 })).toBe(false)
    expect(isVersionedBridgeRequest({ ...request, action: 'skills.list', payload: { target: 'codex' } })).toBe(true)
  })

  it('accepts skills.install.zip with a bounded path and 1-2 whitelisted targets', () => {
    const valid = { zipPath: 'C:/downloads/pdf-tools.zip', targets: ['claude', 'codex'] }
    expect(isVersionedBridgePayload('skills.install.zip', valid)).toBe(true)
    expect(isVersionedBridgePayload('skills.install.zip', { ...valid, targets: ['claude'] })).toBe(true)
    expect(isVersionedBridgePayload('skills.install.zip', { ...valid, zipPath: '' })).toBe(false)
    expect(isVersionedBridgePayload('skills.install.zip', { ...valid, zipPath: 'x'.repeat(4097) })).toBe(false)
    expect(isVersionedBridgePayload('skills.install.zip', { ...valid, targets: [] })).toBe(false)
    expect(isVersionedBridgePayload('skills.install.zip', { ...valid, targets: ['claude', 'dsh'] })).toBe(false)
    expect(isVersionedBridgePayload('skills.install.zip', { ...valid, targets: ['claude', 'claude'] })).toBe(false)
    expect(isVersionedBridgePayload('skills.install.zip', { zipPath: 'C:/x.zip', targets: ['claude'], extra: 1 })).toBe(false)
    expect(bridgeCommandByActionV2['skills.install.zip']).toBe('skills_install_zip')
  })

  it('accepts skills.uninstall and skills.sync with safe skill names', () => {
    expect(isVersionedBridgePayload('skills.uninstall', { target: 'claude', name: 'pdf-tools' })).toBe(true)
    expect(isVersionedBridgePayload('skills.uninstall', { target: 'dsh', name: 'pdf-tools' })).toBe(false)
    expect(isVersionedBridgePayload('skills.uninstall', { target: 'claude', name: '../escape' })).toBe(false)
    expect(isVersionedBridgePayload('skills.uninstall', { target: 'claude', name: 'a\\b' })).toBe(false)
    expect(isVersionedBridgePayload('skills.uninstall', { target: 'claude', name: '' })).toBe(false)
    expect(isVersionedBridgePayload('skills.uninstall', { target: 'claude' })).toBe(false)

    expect(isVersionedBridgePayload('skills.sync', { srcTarget: 'claude', dstTarget: 'codex', name: 'pdf-tools' })).toBe(true)
    expect(isVersionedBridgePayload('skills.sync', { srcTarget: 'claude', dstTarget: 'codex', name: '.' })).toBe(false)
    expect(isVersionedBridgePayload('skills.sync', { srcTarget: 'claude', dstTarget: 'dsh', name: 'pdf-tools' })).toBe(false)
    expect(isVersionedBridgePayload('skills.sync', { srcTarget: 'claude', name: 'pdf-tools' })).toBe(false)
    expect(bridgeCommandByActionV2['skills.list']).toBe('skills_list')
    expect(bridgeCommandByActionV2['skills.uninstall']).toBe('skills_uninstall')
    expect(bridgeCommandByActionV2['skills.sync']).toBe('skills_sync')
  })
})
