import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { HarnessTaskPanel } from '../src/client/model-agent/HarnessTaskPanel'
import type { DesktopBridgeLike } from '../src/client/desktop-bridge'

function bridgeFixture() {
  const requestV2 = vi.fn(async (action: string) => action === 'harness.status' ? { state: 'idle' } : { state: 'running', pid: 7 })
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
})
