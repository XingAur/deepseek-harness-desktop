import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { App } from './App'
import type { BootstrapReply, RuntimeClient, RuntimeEvent } from './runtime-contract'

function fakeRuntime() {
  let listener: ((event: RuntimeEvent) => void) | undefined
  const runtime: RuntimeClient = {
    bootstrapRuntime: vi.fn(async (): Promise<BootstrapReply> => ({ operationId: 'op-1', phase: 'checking' })),
    cancelRuntime: vi.fn(async () => undefined),
    repairRuntime: vi.fn(async (): Promise<BootstrapReply> => ({ operationId: 'repair-1', phase: 'checking' })),
    exportDiagnostics: vi.fn(async () => 'C:\\Temp\\dsh-diagnostics.zip'),
    subscribeRuntimeProgress: vi.fn(async (next) => { listener = next; return () => undefined }),
  }
  return { runtime, emit: (event: RuntimeEvent) => listener?.(event) }
}

describe('App', () => {
  it('starts bootstrap and renders download progress with cancel', async () => {
    const { runtime, emit } = fakeRuntime()
    render(<App runtime={runtime} />)
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
    render(<App runtime={runtime} />)
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
})
