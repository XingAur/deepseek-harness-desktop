import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { App } from './App'
import type { BootstrapReply, RuntimeClient, RuntimeEvent } from './runtime-contract'
import type { WindowControls } from './window-client'

function fakeRuntime() {
  let listener: ((event: RuntimeEvent) => void) | undefined
  const runtime: RuntimeClient = {
    bootstrapRuntime: vi.fn(async (): Promise<BootstrapReply> => ({
      operationId: 'op-1', phase: 'checking', rendererUrl: null,
    })),
    cancelRuntime: vi.fn(async () => undefined),
    repairRuntime: vi.fn(async (): Promise<BootstrapReply> => ({
      operationId: 'repair-1', phase: 'checking', rendererUrl: null,
    })),
    exportDiagnostics: vi.fn(async () => 'C:\\Temp\\dsh-diagnostics.zip'),
    subscribeRuntimeProgress: vi.fn(async (next) => { listener = next; return () => undefined }),
  }
  return { runtime, emit: (event: RuntimeEvent) => listener?.(event) }
}

function fakeWindowControls(): WindowControls {
  return {
    close: vi.fn(async () => undefined),
    minimize: vi.fn(async () => undefined),
    toggleMaximize: vi.fn(async () => undefined),
    startDragging: vi.fn(async () => undefined),
  }
}

describe('App', () => {
  it('restores an already-ready workbench without waiting for another event', async () => {
    const { runtime } = fakeRuntime()
    vi.mocked(runtime.bootstrapRuntime).mockResolvedValue({
      operationId: 'op-ready',
      phase: 'ready',
      rendererUrl: 'http://127.0.0.1:39000/?dsh-desktop-mode=advanced',
    })

    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)

    const frame = await screen.findByTitle('DeepSeek Harness 工作台')
    expect(frame).toHaveAttribute('src', expect.stringContaining('127.0.0.1:39000'))
  })

  it('starts bootstrap and renders download progress with cancel', async () => {
    const { runtime, emit } = fakeRuntime()
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    await waitFor(() => expect(runtime.bootstrapRuntime).toHaveBeenCalled())
    emit({
      kind: 'progress',
      payload: { operationId: 'op-1', phase: 'downloading', completed: 25, total: 100, message: '正在下载' },
    })
    expect(await screen.findByText('25%')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(runtime.cancelRuntime).toHaveBeenCalled()
  })

  it('shows retry, repair and diagnostic actions after failure', async () => {
    const { runtime, emit } = fakeRuntime()
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    await waitFor(() => expect(runtime.bootstrapRuntime).toHaveBeenCalled())
    emit({
      kind: 'failure', operationId: 'op-1',
      payload: { code: 'process', message: '启动失败', recoverable: true },
    })
    expect(await screen.findByRole('button', { name: '重试' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: '导出诊断' }))
    expect(await screen.findByText(/dsh-diagnostics\.zip/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '修复运行时' }))
    await waitFor(() => expect(runtime.repairRuntime).toHaveBeenCalled())
  })

  it('keeps the trusted title bar when the workbench becomes ready', async () => {
    const { runtime, emit } = fakeRuntime()
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    await waitFor(() => expect(runtime.bootstrapRuntime).toHaveBeenCalled())
    expect(screen.getByText('DeepSeek Harness Desktop')).toBeInTheDocument()

    emit({
      kind: 'ready', operationId: 'op-1',
      rendererUrl: 'http://127.0.0.1:39000/?dsh-desktop-mode=advanced',
    })

    const frame = await screen.findByTitle('DeepSeek Harness 工作台')
    expect(frame).toHaveAttribute('src', expect.stringContaining('127.0.0.1:39000'))
    expect(screen.getByText('DeepSeek Harness Desktop')).toBeInTheDocument()
  })

  it('returns from a ready workbench to recovery when the Runtime exits', async () => {
    const { runtime, emit } = fakeRuntime()
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    await waitFor(() => expect(runtime.bootstrapRuntime).toHaveBeenCalled())
    emit({ kind: 'ready', operationId: 'op-1', rendererUrl: 'http://127.0.0.1:39000/' })
    expect(await screen.findByTitle('DeepSeek Harness 工作台')).toBeInTheDocument()

    emit({
      kind: 'failure', operationId: 'op-1',
      payload: { code: 'process', message: 'DeepSeek Harness Runtime 已退出', recoverable: true },
    })

    expect(await screen.findByRole('heading', { name: '启动遇到问题' })).toBeInTheDocument()
    expect(screen.queryByTitle('DeepSeek Harness 工作台')).not.toBeInTheDocument()
    expect(screen.getByText('DeepSeek Harness Desktop')).toBeInTheDocument()
  })
})
