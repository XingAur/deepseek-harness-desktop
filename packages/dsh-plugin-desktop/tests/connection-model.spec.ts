import { describe, expect, it } from 'vitest'
import {
  createConnectionDraft,
  editConnectionDraft,
  normalizeConnectionProfile,
  prepareConnectionDraft,
  validateConnectionDraft,
} from '../src/client/model-agent/connection-model'

describe('connection model', () => {
  it('creates editable defaults for every supported connection type', () => {
    expect(createConnectionDraft('mcp', 'stdio')).toMatchObject({
      kind: 'mcp', transport: 'stdio', templateId: 'custom', workingDirectoryPolicy: 'workspace',
    })
    expect(createConnectionDraft('http-api', 'http')).toMatchObject({
      kind: 'http-api', transport: 'http', templateId: 'custom', workingDirectoryPolicy: 'none',
    })
    expect(createConnectionDraft('database', 'database')).toMatchObject({
      kind: 'database', transport: 'database', templateId: 'database', readOnly: true,
      databaseType: 'postgresql', host: 'localhost', port: 5432, encoding: 'UTF-8', testQuery: 'SELECT 1',
    })
  })

  it('builds a credential-free database endpoint from structured fields', () => {
    const draft = {
      ...createConnectionDraft('database', 'database'),
      displayName: 'HIS 只读库', host: 'db.internal', port: 5432, databaseName: 'his', username: 'readonly',
    }
    const prepared = prepareConnectionDraft(draft)

    expect(prepared.endpoint).toBe('postgresql://db.internal:5432/his')
    expect(prepared.endpoint).not.toContain('readonly')
    expect(validateConnectionDraft(prepared)).toEqual([])
  })

  it('validates type-specific transport, endpoint, command, and safe environment names', () => {
    expect(validateConnectionDraft({
      ...createConnectionDraft('mcp', 'stdio'), displayName: '项目记忆', command: 'node', args: ['server.js'],
      environmentKeys: ['PROJECT_ROOT'],
    })).toEqual([])
    expect(validateConnectionDraft({
      ...createConnectionDraft('http-api', 'http'), displayName: '内部 API', endpoint: 'http://api.internal', healthPath: 'health',
    })).toEqual(expect.arrayContaining(['仅允许 HTTPS 地址，回环地址可使用 HTTP', '健康检查路径必须以 / 开头']))
    expect(validateConnectionDraft({
      ...createConnectionDraft('mcp', 'stdio'), displayName: '不安全 MCP', command: '', environmentKeys: ['API_TOKEN=secret'],
    })).toEqual(expect.arrayContaining(['MCP stdio 连接必须填写命令', '环境变量只能填写名称，不能填写值']))
  })

  it('adapts legacy profiles without rewriting or inventing readiness', () => {
    expect(normalizeConnectionProfile({
      profileId: 'yunxiao-readonly', kind: 'mcp', providerId: 'yunxiao', displayName: '云效需求读取',
      endpoint: 'https://devops.aliyun.com', readOnly: true, enabled: true,
    })).toMatchObject({
      source: 'legacy', transport: 'http', templateId: 'yunxiao', latestTest: { summary: '未测试' },
    })
  })

  it('creates an exact safe edit payload without response-only fields', () => {
    const profile = normalizeConnectionProfile({
      profileId: 'custom-mcp', kind: 'mcp', transport: 'stdio', source: 'custom', templateId: 'custom',
      displayName: '项目记忆', command: 'node', args: ['server.js'], environmentKeys: ['PROJECT_ROOT'],
      workingDirectoryPolicy: 'workspace', endpoint: '', healthPath: '', readOnly: true, enabled: true,
    })

    expect(editConnectionDraft(profile)).toEqual({
      profileId: 'custom-mcp', kind: 'mcp', transport: 'stdio', templateId: 'custom', displayName: '项目记忆',
      endpoint: '', command: 'node', args: ['server.js'], environmentKeys: ['PROJECT_ROOT'],
      workingDirectoryPolicy: 'workspace', healthPath: '', readOnly: true, enabled: true,
    })
    expect(editConnectionDraft(profile)).not.toHaveProperty('source')
    expect(editConnectionDraft(profile)).not.toHaveProperty('latestTest')
  })
})
