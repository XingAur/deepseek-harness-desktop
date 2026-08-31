import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { HarnessChatSurface } from '../src/client/harness/HarnessChatSurface'
import type { DesktopBridgeLike } from '../src/client/desktop-bridge'

function bridgeFixture(): DesktopBridgeLike {
  return {
    request: vi.fn(),
    requestV2: vi.fn(async (action: string) => {
      if (action === 'harness.pick-evidence-files') return ['/tmp/需求截图.png', '/tmp/原始文档.docx']
      if (action === 'harness.connection.list') return [{
        profileId: 'yunxiao-main', kind: 'mcp', providerId: 'yunxiao',
        displayName: '云效需求库', endpoint: 'yunxiao', readOnly: true, enabled: true,
      }]
      return { taskId: 'harness-task-1', status: 'collecting' }
    }) as DesktopBridgeLike['requestV2'],
    dispose: vi.fn(),
  }
}

describe('HarnessChatSurface', () => {
  it('keeps the Harness task entry in the main conversation surface', async () => {
    const bridge = bridgeFixture()
    render(<HarnessChatSurface bridge={bridge} workspaceId="w-1" renderConversation={() => <div data-testid="official-conversation" />} />)

    expect(screen.getByTestId('official-conversation')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '开始 Harness 任务' }))

    expect(screen.getByRole('region', { name: 'Harness 任务' })).toBeInTheDocument()
    expect(screen.getByLabelText('任务描述')).toBeInTheDocument()
    expect(screen.queryByLabelText('目标项目绝对路径')).toBeNull()
    expect(await screen.findByText('云效需求库')).toBeVisible()

    fireEvent.change(screen.getByLabelText('任务描述'), { target: { value: '修复住院结算页面的金额显示问题' } })
    fireEvent.click(screen.getByRole('button', { name: '开始执行' }))

    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith(
      'harness.chat.start',
      undefined,
        expect.objectContaining({ prompt: '修复住院结算页面的金额显示问题', workspaceId: 'w-1' }),
    ))
  })

  it('uses maintained business capabilities instead of asking for generic MCP links', async () => {
    const bridge = bridgeFixture()
    render(<HarnessChatSurface bridge={bridge} renderConversation={() => <div />} />)
    fireEvent.click(screen.getByRole('button', { name: '开始 Harness 任务' }))

    expect(await screen.findByText('云效需求')).toBeVisible()
    expect(screen.getByText('GitLab 代码')).toBeVisible()
    expect(screen.getByText('数据库维护')).toBeVisible()
    expect(screen.queryByLabelText('MCP 地址')).toBeNull()
    expect(screen.queryByLabelText('凭证引用')).toBeNull()
  })

  it('captures the Yunxiao source and selected local materials with the chat task', async () => {
    const bridge = bridgeFixture()
    render(<HarnessChatSurface bridge={bridge} renderConversation={() => <div />} />)
    fireEvent.click(screen.getByRole('button', { name: '开始 Harness 任务' }))
    fireEvent.change(screen.getByLabelText('任务描述'), { target: { value: '按需求完成修复并验证' } })
    fireEvent.change(screen.getByLabelText('关联云效需求'), { target: { value: 'DFHIS-12345' } })
    fireEvent.click(screen.getByRole('button', { name: '添加需求图片 / 文档 / 附件' }))
    await waitFor(() => expect(screen.getByText('已添加 2 个文件')).toBeVisible())
    fireEvent.click(screen.getByRole('button', { name: '开始执行' }))

    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith(
      'harness.chat.start',
      undefined,
      expect.objectContaining({
        prompt: '按需求完成修复并验证',
        yunxiaoSource: 'DFHIS-12345',
        evidencePaths: ['/tmp/需求截图.png', '/tmp/原始文档.docx'],
      }),
    ))
  })

  it('keeps execution state and genuine Harness blockers in the main chat surface', async () => {
    const bridge = bridgeFixture()
    let statusCalls = 0
    bridge.requestV2 = vi.fn(async (action: string) => {
      if (action === 'harness.connection.list') return []
      if (action === 'harness.chat.start') return { state: 'running' }
      if (action === 'harness.status') {
        statusCalls += 1
        return statusCalls > 0 ? { state: 'blocked', blockers: ['部分退规则无法从现有证据确认'], intake: { packageDir: '/tmp/CHAT-1' } } : { state: 'running' }
      }
      if (action === 'harness.archive-answers') return '/tmp/CHAT-1/analysis/business_answers.md'
      return null
    }) as DesktopBridgeLike['requestV2']
    render(<HarnessChatSurface bridge={bridge} renderConversation={() => <div />} />)
    fireEvent.click(screen.getByRole('button', { name: '开始 Harness 任务' }))
    fireEvent.change(screen.getByLabelText('任务描述'), { target: { value: '完成部分退需求' } })
    fireEvent.click(screen.getByRole('button', { name: '开始执行' }))

    await waitFor(() => expect(screen.getByText('Harness 状态：已阻断')).toBeVisible())
    expect(screen.getByText('部分退规则无法从现有证据确认')).toBeVisible()
    fireEvent.change(screen.getByLabelText('业务确认'), { target: { value: '按当前医院配置执行' } })
    fireEvent.click(screen.getByRole('button', { name: '提交业务确认' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('harness.archive-answers', undefined, { archiveRoot: '/tmp/CHAT-1', answers: '按当前医院配置执行' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith(
      'harness.chat.start',
      undefined,
      expect.objectContaining({ prompt: '完成部分退需求', archiveRoot: '/tmp/CHAT-1' }),
    ))
    expect(await screen.findByText('业务确认已保存，Harness 正在按最新口径重新决策。')).toBeVisible()
  })

  it('keeps an archived business answer safe and retries only the decision restart', async () => {
    const bridge = bridgeFixture()
    let startCalls = 0
    bridge.requestV2 = vi.fn(async (action: string) => {
      if (action === 'harness.connection.list') return []
      if (action === 'harness.chat.start') {
        startCalls += 1
        if (startCalls === 2) throw new Error('Harness host unavailable')
        return { state: 'running' }
      }
      if (action === 'harness.status') {
        return { state: 'blocked', blockers: ['需要确认历史口径'], intake: { packageDir: '/tmp/CHAT-RETRY' } }
      }
      if (action === 'harness.archive-answers') return '/tmp/CHAT-RETRY/analysis/business_answers.md'
      return null
    }) as DesktopBridgeLike['requestV2']

    render(<HarnessChatSurface bridge={bridge} renderConversation={() => <div />} />)
    fireEvent.click(screen.getByRole('button', { name: '开始 Harness 任务' }))
    fireEvent.change(screen.getByLabelText('任务描述'), { target: { value: '完成历史口径适配' } })
    fireEvent.click(screen.getByRole('button', { name: '开始执行' }))

    await waitFor(() => expect(screen.getByText('需要确认历史口径')).toBeVisible())
    fireEvent.change(screen.getByLabelText('业务确认'), { target: { value: '继续兼容历史路径' } })
    fireEvent.click(screen.getByRole('button', { name: '提交业务确认' }))

    expect(await screen.findByText(/业务确认已安全保存，但 Harness 重新决策启动失败/)).toBeVisible()
    expect(screen.getByRole('button', { name: '重新启动决策' })).toBeEnabled()

    fireEvent.click(screen.getByRole('button', { name: '重新启动决策' }))
    expect(await screen.findByText('业务确认已保存，Harness 正在按最新口径重新决策。')).toBeVisible()

    const archiveCalls = vi.mocked(bridge.requestV2).mock.calls.filter(([action]) => action === 'harness.archive-answers')
    const restartCalls = vi.mocked(bridge.requestV2).mock.calls.filter(([action]) => action === 'harness.chat.start')
    expect(archiveCalls).toHaveLength(1)
    expect(restartCalls).toHaveLength(3)
  })
})
