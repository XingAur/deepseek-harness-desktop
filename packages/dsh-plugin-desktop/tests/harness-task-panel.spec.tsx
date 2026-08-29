import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { HarnessTaskPanel } from '../src/client/model-agent/HarnessTaskPanel'
import type { DesktopBridgeLike } from '../src/client/desktop-bridge'

function bridgeFixture(statusByAction: Record<string, unknown> = {}) {
  const requestV2 = vi.fn(async (action: string) => {
    if (action in statusByAction) return statusByAction[action]
    return action === 'harness.status' ? { state: 'idle' } : { state: 'running', pid: 7 }
  })
  return { requestV2, request: vi.fn(), dispose: vi.fn() } as unknown as DesktopBridgeLike & { requestV2: typeof requestV2 }
}

describe('HarnessTaskPanel', () => {
  it('requires generated task context before exposing execute', async () => {
    const bridge = bridgeFixture()
    render(<HarnessTaskPanel bridge={bridge} />)
    expect(await screen.findByText(/还缺少 5 项/)).toBeVisible()
    expect(screen.getByRole('button', { name: '按 Harness 决策执行' })).toBeDisabled()
  })

  it('forwards only the declared Harness task fields to the desktop bridge', async () => {
    const bridge = bridgeFixture()
    render(<HarnessTaskPanel bridge={bridge} />)
    await screen.findByText(/还缺少 5 项/)
    const values: Record<string, string> = {
      taskContractPath: '/tmp/task.json',
      understandingPath: '/tmp/understanding.json',
      worktreeRoot: '/tmp/project',
      knowledgeHome: '/tmp/knowledge',
      authorizationId: 'DFHIS-32178-change-1',
    }
    const labels: Record<string, string> = {
      taskContractPath: '任务契约文件绝对路径',
      understandingPath: '需求理解文件绝对路径',
      worktreeRoot: '目标项目绝对路径',
      knowledgeHome: '知识库目录绝对路径',
      authorizationId: '执行授权编号',
    }
    for (const [key, value] of Object.entries(values)) fireEvent.change(screen.getByLabelText(labels[key]), { target: { value } })
    fireEvent.click(screen.getByRole('button', { name: '按 Harness 决策执行' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('harness.start', undefined, expect.objectContaining(values)))
  })

  it('uses the selected archive package instead of asking for generated paths', async () => {
    const bridge = bridgeFixture()
    render(<HarnessTaskPanel bridge={bridge} />)
    fireEvent.change(screen.getByLabelText('Harness 归档根目录'), { target: { value: '/Users/test/harness/DFHIS-32178/harness' } })
    await screen.findByText(/还缺少 3 项/)
    fireEvent.change(screen.getByLabelText('目标项目绝对路径'), { target: { value: '/Users/test/project' } })
    fireEvent.change(screen.getByLabelText('知识库目录绝对路径'), { target: { value: '/Users/test/knowledge' } })
    fireEvent.change(screen.getByLabelText('执行授权编号'), { target: { value: 'DFHIS-32178-change-1' } })
    fireEvent.click(screen.getByRole('button', { name: '按 Harness 决策执行' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('harness.start', undefined, expect.objectContaining({
      archiveRoot: '/Users/test/harness/DFHIS-32178/harness',
      worktreeRoot: '/Users/test/project',
      knowledgeHome: '/Users/test/knowledge',
    })))
  })

  it('archives a Yunxiao source with the currently selected model before executing', async () => {
    const bridge = bridgeFixture()
    render(<HarnessTaskPanel bridge={bridge} />)
    fireEvent.change(screen.getByLabelText('云效需求 URL 或工作项 ID'), { target: { value: 'DFHIS-39999' } })
    fireEvent.change(screen.getByLabelText('Harness 归档根目录'), { target: { value: '/Users/test/harness-archives' } })
    fireEvent.change(screen.getByLabelText('当前统一模型 ID'), { target: { value: 'deepseek-reasoner' } })
    fireEvent.click(screen.getByRole('button', { name: '只读归档并起草分析文档' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('harness.intake', undefined, {
      source: 'DFHIS-39999',
      archiveRoot: '/Users/test/harness-archives',
      includeComments: true,
      selectedModelId: 'deepseek-reasoner',
      agentBackend: 'host-bridge',
    }))
    expect(await screen.findByText(/云效归档已启动/)).toBeVisible()
  })

  it('fills the archive root from the native directory picker', async () => {
    const bridge = bridgeFixture({ 'harness.pick-archive-root': '/Users/test/chosen-archive-root' })
    render(<HarnessTaskPanel bridge={bridge} />)
    fireEvent.click(screen.getByRole('button', { name: '选择本机目录…' }))
    await waitFor(() => expect((screen.getByLabelText('Harness 归档根目录') as HTMLInputElement).value).toBe('/Users/test/chosen-archive-root'))
  })

  it('keeps a cancelled directory picker silent', async () => {
    const bridge = bridgeFixture({ 'harness.pick-archive-root': null })
    render(<HarnessTaskPanel bridge={bridge} />)
    fireEvent.click(screen.getByRole('button', { name: '选择本机目录…' }))
    await waitFor(() => expect(bridge.requestV2).toHaveBeenCalledWith('harness.pick-archive-root'))
    expect((screen.getByLabelText('Harness 归档根目录') as HTMLInputElement).value).toBe('')
  })

  it('surfaces the intake snapshot with generated documents and open questions', async () => {
    let status: unknown = { state: 'running', pid: 9 }
    const requestV2 = vi.fn(async (action: string) => {
      if (action === 'harness.status') return status
      if (action === 'harness.pick-archive-root') return null
      return { state: 'running', pid: 9 }
    })
    const bridge = { requestV2, request: vi.fn(), dispose: vi.fn() } as unknown as DesktopBridgeLike & { requestV2: typeof requestV2 }
    render(<HarnessTaskPanel bridge={bridge} />)
    fireEvent.change(screen.getByLabelText('云效需求 URL 或工作项 ID'), { target: { value: 'DFHIS-39999' } })
    fireEvent.change(screen.getByLabelText('Harness 归档根目录'), { target: { value: '/Users/test/harness-archives' } })
    fireEvent.click(screen.getByRole('button', { name: '只读归档并起草分析文档' }))
    await waitFor(() => expect(requestV2).toHaveBeenCalledWith('harness.intake', undefined, expect.objectContaining({ source: 'DFHIS-39999' })))

    status = {
      state: 'completed',
      intake: {
        ticketId: 'DFHIS-39999',
        packageDir: '/Users/test/harness-archives/DFHIS-39999/harness',
        generationStatus: 'generated',
        generatedCount: 8,
        pendingCount: 17,
        openQuestions: ['重打记录是否需要按操作员过滤？'],
      },
    }
    fireEvent.click(screen.getByRole('button', { name: '刷新状态' }))
    expect(await screen.findByText(/已归档 DFHIS-39999/)).toBeVisible()
    expect(screen.getByText(/已起草 8 篇分析文档/)).toBeVisible()
    expect(screen.getByText(/重打记录是否需要按操作员过滤/)).toBeVisible()
    await waitFor(() => expect((screen.getByLabelText('Harness 归档根目录') as HTMLInputElement).value).toBe('/Users/test/harness-archives/DFHIS-39999/harness'))
  })

  it('reports a failed model draft as retryable without hiding the archive', async () => {
    let status: unknown = { state: 'running', pid: 9 }
    const requestV2 = vi.fn(async (action: string) => {
      if (action === 'harness.status') return status
      return { state: 'running', pid: 9 }
    })
    const bridge = { requestV2, request: vi.fn(), dispose: vi.fn() } as unknown as DesktopBridgeLike & { requestV2: typeof requestV2 }
    render(<HarnessTaskPanel bridge={bridge} />)
    fireEvent.change(screen.getByLabelText('云效需求 URL 或工作项 ID'), { target: { value: 'DFHIS-39999' } })
    fireEvent.change(screen.getByLabelText('Harness 归档根目录'), { target: { value: '/Users/test/harness-archives' } })
    fireEvent.click(screen.getByRole('button', { name: '只读归档并起草分析文档' }))
    await waitFor(() => expect(requestV2).toHaveBeenCalledWith('harness.intake', undefined, expect.objectContaining({ source: 'DFHIS-39999' })))

    status = {
      state: 'completed',
      intake: {
        ticketId: 'DFHIS-39999',
        packageDir: '/Users/test/harness-archives/DFHIS-39999/harness',
        generationStatus: 'failed',
        generationErrorCode: 'worker_backend_unavailable',
        generatedCount: 0,
      },
    }
    fireEvent.click(screen.getByRole('button', { name: '刷新状态' }))
    expect(await screen.findByText(/模型起草未完成/)).toBeVisible()
    expect(screen.getByText(/worker_backend_unavailable/)).toBeVisible()
  })

  it('shows understanding blockers and submits business answers into the package', async () => {
    let status: unknown = { state: 'running', pid: 9 }
    const requestV2 = vi.fn(async (action: string, _payload?: unknown, params?: unknown) => {
      if (action === 'harness.status') return status
      if (action === 'harness.archive-answers') return '/Users/test/harness-archives/DFHIS-39999/harness/analysis/business_answers.md'
      void params
      return { state: 'running', pid: 9 }
    })
    const bridge = { requestV2, request: vi.fn(), dispose: vi.fn() } as unknown as DesktopBridgeLike & { requestV2: typeof requestV2 }
    render(<HarnessTaskPanel bridge={bridge} />)
    fireEvent.change(screen.getByLabelText('Harness 归档根目录'), { target: { value: '/Users/test/harness-archives/DFHIS-39999/harness' } })

    status = {
      state: 'blocked',
      errorCode: 'requirement_understanding_incomplete',
      blockers: [
        'business_question:重打记录是否需要按操作员过滤？',
        'verification_baseline_missing:目标项目没有可用的 python unittest 验证基线',
      ],
    }
    fireEvent.click(screen.getByRole('button', { name: '刷新状态' }))
    expect(await screen.findByText(/理解门禁待确认（2 项）/)).toBeVisible()
    expect(screen.getByText(/重打记录是否需要按操作员过滤/)).toBeVisible()

    fireEvent.change(screen.getByLabelText('业务答复'), { target: { value: '重打记录按操作员过滤，保留最近 3 个月。' } })
    fireEvent.click(screen.getByRole('button', { name: '提交业务答复' }))
    await waitFor(() => expect(requestV2).toHaveBeenCalledWith('harness.archive-answers', undefined, {
      archiveRoot: '/Users/test/harness-archives/DFHIS-39999/harness',
      answers: '重打记录按操作员过滤，保留最近 3 个月。',
    }))
    expect(await screen.findByText(/业务答复已写入任务包/)).toBeVisible()
  })
})
