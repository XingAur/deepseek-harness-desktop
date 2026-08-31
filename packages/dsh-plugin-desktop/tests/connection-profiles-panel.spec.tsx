import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ConnectionProfilesPanel } from '../src/client/model-agent/ConnectionProfilesPanel'
import type { DesktopBridgeLike } from '../src/client/desktop-bridge'

function fixture(mode?: 'preview'): DesktopBridgeLike {
  return {
    ...(mode === undefined ? {} : { mode }),
    request: vi.fn(),
    requestV2: vi.fn(async (action: string, _context?: unknown, payload?: Record<string, unknown>) => {
      if (action === 'harness.connection.list') return [{
        profileId: 'internal-api', kind: 'http-api', transport: 'http', source: 'custom', templateId: 'custom',
        displayName: '内部 API', endpoint: 'https://api.example.test', command: '', args: [], environmentKeys: [],
        workingDirectoryPolicy: 'none', healthPath: '/health', readOnly: true, enabled: true,
        latestTest: { summary: '未测试', layers: [] },
      }]
      if (action === 'harness.connection.test') return {
        profileId: 'internal-api', tested: true, testKind: 'layered', summary: '网络可达，认证未配置', message: '',
        layers: [
          { id: 'configuration', label: '配置', state: 'passed', message: '配置有效' },
          { id: 'network', label: '网络', state: 'passed', message: 'HTTPS 可达' },
          { id: 'protocol', label: '协议', state: 'passed', message: '健康检查成功' },
          { id: 'authentication', label: '认证', state: 'not-configured', message: '未选择凭证' },
          { id: 'permission', label: '权限', state: 'not-tested', message: '未验证' },
        ],
      }
      if (action === 'credential.put') return { credentialId: 'credential-safe', status: 'configured' }
      if (action === 'harness.connection.save') return {
        ...payload, profileId: 'saved-profile', source: 'custom', latestTest: { summary: '未测试', layers: [] },
      }
      return {}
    }) as DesktopBridgeLike['requestV2'],
    dispose: vi.fn(),
  }
}

describe('ConnectionProfilesPanel', () => {
  it('renders five independent test layers without promoting network success', async () => {
    const bridge = fixture()
    render(<ConnectionProfilesPanel bridge={bridge} managedMcp={[]} />)
    fireEvent.click(await screen.findByRole('button', { name: '测试 内部 API' }))

    expect(await screen.findByText('网络可达，认证未配置')).toBeVisible()
    expect(screen.getByText('配置有效')).toBeVisible()
    expect(screen.getByText('HTTPS 可达')).toBeVisible()
    expect(screen.getByText('健康检查成功')).toBeVisible()
    expect(screen.getByText('未选择凭证')).toBeVisible()
    expect(screen.getByText('未验证')).toBeVisible()
  })

  it('keeps direct browser preview read-only and never sends profile mutations', async () => {
    const bridge = fixture('preview')
    render(<ConnectionProfilesPanel bridge={bridge} managedMcp={[]} />)
    fireEvent.click(await screen.findByRole('button', { name: '新增连接' }))

    expect(screen.getByText(/只读预览可以填写表单/)).toBeVisible()
    expect(screen.getByRole('button', { name: '正式桌面可保存' })).toBeDisabled()
    await waitFor(() => expect(bridge.requestV2).not.toHaveBeenCalledWith('harness.connection.save', expect.anything(), expect.anything()))
  })

  it('shows focused Yunxiao and GitLab token forms instead of generic MCP internals', async () => {
    const bridge = fixture()
    render(<ConnectionProfilesPanel bridge={bridge} managedMcp={[]} />)
    fireEvent.click(await screen.findByRole('button', { name: '新增连接' }))
    fireEvent.change(screen.getByLabelText('连接方案'), { target: { value: 'yunxiao' } })

    expect(screen.getByLabelText('云效服务地址')).toBeVisible()
    expect(screen.getByLabelText('云效个人令牌')).toHaveAttribute('type', 'password')
    expect(screen.queryByLabelText('命令')).toBeNull()
    expect(screen.queryByLabelText('环境变量名称')).toBeNull()

    fireEvent.change(screen.getByLabelText('连接方案'), { target: { value: 'gitlab' } })
    expect(screen.getByLabelText('GitLab 地址')).toBeVisible()
    expect(screen.getByLabelText('GitLab 个人访问令牌')).toHaveAttribute('type', 'password')
  })

  it('uses structured database fields and stores only a credential reference in the profile', async () => {
    const bridge = fixture()
    render(<ConnectionProfilesPanel bridge={bridge} managedMcp={[]} />)
    fireEvent.click(await screen.findByRole('button', { name: '新增连接' }))
    fireEvent.change(screen.getByLabelText('连接方案'), { target: { value: 'database' } })

    expect(screen.getByLabelText('数据库类型')).toBeVisible()
    expect(screen.getByLabelText('主机')).toBeVisible()
    expect(screen.getByLabelText('端口')).toBeVisible()
    expect(screen.getByLabelText('数据库名称')).toBeVisible()
    expect(screen.getByLabelText('用户名')).toBeVisible()
    expect(screen.getByLabelText('密码')).toHaveAttribute('type', 'password')
    expect(screen.getByLabelText('编码')).toBeVisible()
    expect(screen.getByLabelText('连接测试查询语句')).toBeVisible()
    expect(screen.queryByLabelText('安全凭证引用')).toBeNull()

    fireEvent.change(screen.getByLabelText('连接名称'), { target: { value: 'HIS 只读库' } })
    fireEvent.change(screen.getByLabelText('主机'), { target: { value: 'db.internal' } })
    fireEvent.change(screen.getByLabelText('数据库名称'), { target: { value: 'his' } })
    fireEvent.change(screen.getByLabelText('用户名'), { target: { value: 'readonly' } })
    fireEvent.change(screen.getByLabelText('密码'), { target: { value: 'db-secret' } })
    fireEvent.click(screen.getByRole('button', { name: '保存连接' }))

    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('credential.put', undefined, { secret: 'db-secret' }))
    const save = vi.mocked(bridge.requestV2).mock.calls.find(([action]) => action === 'harness.connection.save')
    expect(save?.[2]).toMatchObject({
      displayName: 'HIS 只读库', endpoint: 'postgresql://db.internal:5432/his', username: 'readonly', credentialId: 'credential-safe',
    })
    expect(JSON.stringify(save?.[2])).not.toContain('db-secret')
  })
})
