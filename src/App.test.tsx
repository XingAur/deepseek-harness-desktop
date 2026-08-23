import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { StrictMode } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { App } from './App'
import type { AppUpdateEvent, BootstrapReply, DesktopEvent, LocalAppEvent, RuntimeClient, RuntimeEvent, RuntimeFailureCode } from './runtime-contract'
import type { WindowControls } from './window-client'

function fakeRuntime() {
  let listener: ((event: RuntimeEvent) => void) | undefined
  let desktopListener: ((event: DesktopEvent) => void) | undefined
  let appUpdateListener: ((event: AppUpdateEvent) => void) | undefined
  let localAppListener: ((event: LocalAppEvent) => void) | undefined
  const runtime: RuntimeClient = {
    bootstrapRuntime: vi.fn(async (): Promise<BootstrapReply> => ({
      operationId: 'op-1', phase: 'checking', rendererUrl: null,
    })),
    cancelRuntime: vi.fn(async () => undefined),
    repairRuntime: vi.fn(async (): Promise<BootstrapReply> => ({
      operationId: 'repair-1', phase: 'checking', rendererUrl: null,
    })),
    exportDiagnostics: vi.fn(async () => 'C:\\Temp\\dsh-diagnostics.zip'),
    migrationStatus: vi.fn(async () => ({ phase: 'ready' as const })),
    confirmMigration: vi.fn(async () => undefined),
    deferMigration: vi.fn(async () => undefined),
    recoveryStatus: vi.fn(async () => null),
    checkAppUpdate: vi.fn(async () => ({ phase: 'idle' as const })),
    downloadAppUpdate: vi.fn(async () => ({ phase: 'idle' as const })),
    installAppUpdateNow: vi.fn(async () => undefined),
    installAppUpdateOnExit: vi.fn(async () => ({ phase: 'idle' as const })),
    deferAppUpdate: vi.fn(async () => ({ phase: 'idle' as const })),
    openAppUpdateDownload: vi.fn(async () => undefined),
    takeAppUpdateReceipt: vi.fn(async () => null),
    subscribeRuntimeProgress: vi.fn(async (next) => { listener = next; return () => undefined }),
    subscribeDesktopEvents: vi.fn(async (next) => { desktopListener = next; return () => undefined }),
    subscribeAppUpdates: vi.fn(async (next) => { appUpdateListener = next; return () => undefined }),
    subscribeLocalAppEvents: vi.fn(async (next) => { localAppListener = next; return () => undefined }),
  }
  return {
    runtime,
    emit: (event: RuntimeEvent) => listener?.(event),
    emitDesktop: (event: DesktopEvent) => desktopListener?.(event),
    emitAppUpdate: (event: AppUpdateEvent) => appUpdateListener?.(event),
    emitLocalApp: (event: LocalAppEvent) => localAppListener?.(event),
  }
}

function fakeWindowControls(): WindowControls {
  return {
    hide: vi.fn(async () => undefined),
    minimize: vi.fn(async () => undefined),
    toggleMaximize: vi.fn(async () => undefined),
    startDragging: vi.fn(async () => undefined),
  }
}

describe('App', () => {
  it('checks for an application update once when the shell mounts before Runtime is ready', async () => {
    const { runtime } = fakeRuntime()
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)

    await waitFor(() => expect(runtime.checkAppUpdate).toHaveBeenCalledWith('automatic'))
    expect(runtime.checkAppUpdate).toHaveBeenCalledTimes(1)
    expect(runtime.bootstrapRuntime).toHaveBeenCalled()
  })

  it('does not repeat the automatic check under React Strict Mode', async () => {
    const { runtime } = fakeRuntime()
    render(<StrictMode><App runtime={runtime} windowControls={fakeWindowControls()} /></StrictMode>)

    await waitFor(() => expect(runtime.checkAppUpdate).toHaveBeenCalledWith('automatic'))
    expect(runtime.checkAppUpdate).toHaveBeenCalledTimes(1)
    expect(runtime.takeAppUpdateReceipt).toHaveBeenCalledTimes(1)
  })

  it('offers only a trusted DMG download and unsigned warning for macOS manual updates', async () => {
    const { runtime } = fakeRuntime()
    vi.mocked(runtime.checkAppUpdate).mockResolvedValue({
      phase: 'available',
      update: {
        version: '0.1.13',
        notes: '同步新版 DeepSeek Harness，并保留现有 Profile、项目和本地数据。',
        size: 2048,
        mode: 'manual-dmg',
        downloadUrl: 'https://github.com/XingAur/deepseek-harness-desktop/releases/download/desktop-v0.1.13/file_0.1.13_aarch64.dmg',
        developerIdSigned: false,
        notarized: false,
      },
    })
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)

    expect(await screen.findByRole('button', { name: '下载 DMG' })).toBeVisible()
    expect(screen.getByText(/未使用 Apple Developer ID 签名、未经过 Apple 公证/)).toBeVisible()
    expect(screen.getByText(/保留现有 Profile、项目和本地数据/)).toBeVisible()
    expect(screen.getByText(/退出本应用.*拖入“应用程序”.*确认替换/)).toBeVisible()
    expect(screen.getByText(/不要删除 Application Support 中的数据/)).toBeVisible()
    expect(screen.getByText(/Control 点按应用.*隐私与安全性/)).toBeVisible()
    expect(screen.queryByRole('button', { name: '后台下载' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '稍后提醒' })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '下载 DMG' }))
    await waitFor(() => expect(runtime.openAppUpdateDownload).toHaveBeenCalledTimes(1))
    expect(runtime.downloadAppUpdate).not.toHaveBeenCalled()
  })

  it('keeps a manual DMG open failure non-blocking and actionable', async () => {
    const { runtime } = fakeRuntime()
    vi.mocked(runtime.checkAppUpdate).mockResolvedValue({
      phase: 'available',
      update: {
        version: '0.1.13', notes: null, size: 2048, mode: 'manual-dmg',
        downloadUrl: 'https://github.com/XingAur/deepseek-harness-desktop/releases/download/desktop-v0.1.13/file_0.1.13_aarch64.dmg',
        developerIdSigned: false, notarized: false,
      },
    })
    vi.mocked(runtime.openAppUpdateDownload).mockRejectedValue(new Error('无法打开浏览器'))
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)

    fireEvent.click(await screen.findByRole('button', { name: '下载 DMG' }))
    expect(await screen.findByText('应用更新暂时不可用')).toBeVisible()
    expect(screen.getByText(/不影响当前工作台/)).toBeVisible()
  })

  it('offers restart, install on exit, and defer after a signed download', async () => {
    const { runtime, emitDesktop } = fakeRuntime()
    vi.mocked(runtime.checkAppUpdate).mockResolvedValue({
      phase: 'ready',
      update: { version: '0.2.0', notes: '稳定性更新', size: 1024, mode: 'in-app' },
    })
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    await waitFor(() => expect(runtime.bootstrapRuntime).toHaveBeenCalled())
    emitDesktop({
      kind: 'generation-active', generationId: 'op-1',
      snapshot: {
        generationId: 'op-1', phase: 'active', runtimeVersion: '1.8.2',
        rendererUrl: 'http://127.0.0.1:39000/',
        profile: { profileId: 'a', revision: 1, name: '默认' },
      },
    })

    expect(await screen.findByText('DeepSeek Harness 0.2.0 已准备好')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '立即重启安装' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '退出时安装' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '暂不安装' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '退出时安装' }))
    await waitFor(() => expect(runtime.installAppUpdateOnExit).toHaveBeenCalled())
  })

  it('keeps automatic application update failures silent', async () => {
    const { runtime, emitDesktop } = fakeRuntime()
    vi.mocked(runtime.checkAppUpdate).mockRejectedValue({
      code: 'check', message: '更新服务器暂时不可用', recoverable: true,
    })
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    await waitFor(() => expect(runtime.bootstrapRuntime).toHaveBeenCalled())
    emitDesktop({
      kind: 'generation-active', generationId: 'op-1',
      snapshot: {
        generationId: 'op-1', phase: 'active', runtimeVersion: '1.8.2',
        rendererUrl: 'http://127.0.0.1:39000/',
        profile: { profileId: 'a', revision: 1, name: '默认' },
      },
    })

    await waitFor(() => expect(runtime.checkAppUpdate).toHaveBeenCalledWith('automatic'))
    expect(screen.queryByText('应用更新暂时不可用')).not.toBeInTheDocument()
    expect(screen.getByTitle('DeepSeek Harness 工作台')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: '启动遇到问题' })).not.toBeInTheDocument()
  })

  it('shows an actionable banner for a manual native-menu failure', async () => {
    const { runtime, emitDesktop, emitAppUpdate } = fakeRuntime()
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    await waitFor(() => expect(runtime.bootstrapRuntime).toHaveBeenCalled())
    emitDesktop({
      kind: 'generation-active', generationId: 'op-1',
      snapshot: {
        generationId: 'op-1', phase: 'active', runtimeVersion: '1.8.2',
        rendererUrl: 'http://127.0.0.1:39000/',
        profile: { profileId: 'a', revision: 1, name: '默认' },
      },
    })
    await waitFor(() => expect(runtime.checkAppUpdate).toHaveBeenCalledWith('automatic'))
    emitAppUpdate({
      source: 'manual',
      state: { phase: 'failed', update: { code: 'check', message: '更新服务器暂时不可用', recoverable: true } },
    })

    expect(await screen.findByText('应用更新暂时不可用')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    await waitFor(() => expect(runtime.checkAppUpdate).toHaveBeenCalledWith('manual'))
  })

  it('shows a completed application version transition once the workbench is active', async () => {
    const { runtime, emitDesktop } = fakeRuntime()
    vi.mocked(runtime.takeAppUpdateReceipt).mockResolvedValue({
      previousVersion: '0.1.0', targetVersion: '0.2.0', installedAt: '2026-08-19T12:00:00Z',
    })
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    await waitFor(() => expect(runtime.bootstrapRuntime).toHaveBeenCalled())
    emitDesktop({
      kind: 'generation-active', generationId: 'op-1',
      snapshot: {
        generationId: 'op-1', phase: 'active', runtimeVersion: '1.8.2',
        rendererUrl: 'http://127.0.0.1:39000/',
        profile: { profileId: 'a', revision: 1, name: '默认' },
      },
    })

    expect(await screen.findByText('DeepSeek Harness 已更新')).toBeInTheDocument()
    expect(screen.getByText('0.1.0 → 0.2.0')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '知道了' }))
    expect(screen.queryByText('DeepSeek Harness 已更新')).not.toBeInTheDocument()
  })

  it('keeps a deferred migration blocked without bootstrapping a read-only foundation', async () => {
    const { runtime } = fakeRuntime()
    vi.mocked(runtime.migrationStatus).mockResolvedValue({
      phase: 'candidate', source: 'C:\\旧数据', target: 'C:\\新数据',
      bytes: 4096, profiles: 2, workspaces: 3,
    })
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    expect(await screen.findByText('发现旧版桌面数据')).toBeInTheDocument()
    expect(runtime.bootstrapRuntime).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '稍后处理' }))
    await waitFor(() => expect(runtime.deferMigration).toHaveBeenCalled())
    expect(await screen.findByText('数据迁移已暂缓')).toBeInTheDocument()
    expect(runtime.bootstrapRuntime).not.toHaveBeenCalled()
  })

  it('renders verified recovery evidence without offering automatic source replacement', async () => {
    const { runtime } = fakeRuntime()
    vi.mocked(runtime.migrationStatus).mockResolvedValue({ phase: 'ready' })
    vi.mocked(runtime.recoveryStatus).mockResolvedValue({
      source: '/data/state/agent-platform.sqlite3',
      backup: '/data/backups/agent-platform/bundle/agent-platform.sqlite3',
      sha256: '0123456789abcdef'.repeat(4),
      length: 8192,
      schema: 0,
      sidecar: '/data/backups/agent-platform/bundle/metadata.json',
    })

    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)

    expect(await screen.findByText('Agent 数据库需要人工恢复')).toBeInTheDocument()
    expect(screen.getByText('/data/state/agent-platform.sqlite3')).toBeInTheDocument()
    expect(screen.getByText('/data/backups/agent-platform/bundle/agent-platform.sqlite3')).toBeInTheDocument()
    expect(screen.getByText(/0123456789abcdef/)).toBeInTheDocument()
    expect(screen.getByText(/8192/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /恢复|覆盖|替换/ })).not.toBeInTheDocument()
    expect(runtime.bootstrapRuntime).not.toHaveBeenCalled()
  })

  it('shows the fixed blocking message when published recovery evidence is lost', async () => {
    const { runtime } = fakeRuntime()
    vi.mocked(runtime.migrationStatus).mockResolvedValue({ phase: 'ready' })
    vi.mocked(runtime.recoveryStatus).mockRejectedValue({
      code: 'repair-required',
      message: 'Agent 数据库恢复证据已丢失，已阻止启动',
      recoverable: false,
    })

    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)

    expect(await screen.findByText('Agent 数据库恢复证据已丢失，已阻止启动')).toBeVisible()
    expect(runtime.bootstrapRuntime).not.toHaveBeenCalled()
  })

  it('shows version transition and last-known-good recovery events', async () => {
    const { runtime, emitDesktop } = fakeRuntime()
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    await waitFor(() => expect(runtime.bootstrapRuntime).toHaveBeenCalled())
    emitDesktop({
      kind: 'generation-progress', generationId: 'op-1',
      payload: {
        phase: 'preparing-runtime', message: '正在升级', completed: 5, total: 10,
        installedVersion: '1.7.0', requiredVersion: '1.8.2',
      },
    })
    expect(await screen.findByText('Runtime v1.7.0 → v1.8.2')).toBeInTheDocument()
    emitDesktop({
      kind: 'profile-recovered', generationId: 'op-1',
      profile: { profileId: 'a', revision: 3, name: '默认开发环境' },
      reason: '目标 Profile 启动失败',
    })
    expect(await screen.findByText('已恢复到默认开发环境')).toBeInTheDocument()
  })

  it.each([
    ['checking', '版本一致，正在快速启动…'],
    ['fetching-manifest', '发现新版本 0.2.0，正在自动更新…'],
    ['starting', '更新完成（已更新到 0.2.0），正在启动 DeepSeek Harness…'],
  ] as const)('shows %s decision messages', async (phase, message) => {
    const { runtime, emit } = fakeRuntime()
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    await waitFor(() => expect(runtime.bootstrapRuntime).toHaveBeenCalled())
    emit({
      kind: 'progress',
      payload: { operationId: 'op-1', phase, completed: 0, total: null, message },
    })
    expect(await screen.findByText(message)).toBeInTheDocument()
  })

  it('shows first launch and automatic repair copy from runtime progress', async () => {
    const { runtime, emit } = fakeRuntime()
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    await waitFor(() => expect(runtime.bootstrapRuntime).toHaveBeenCalled())
    emit({
      kind: 'progress',
      payload: {
        operationId: 'op-1', phase: 'fetching-manifest', completed: 0, total: null,
        message: '首次使用，需要下载运行组件',
      },
    })
    expect(await screen.findByText('首次使用，需要下载运行组件')).toBeVisible()
    emit({
      kind: 'progress',
      payload: {
        operationId: 'op-1', phase: 'fetching-manifest', completed: 0, total: null,
        message: '本地运行组件需要修复，正在重新下载',
      },
    })
    expect(await screen.findByText('本地运行组件需要修复，正在重新下载')).toBeVisible()
  })

  it('uses the official DeepSeek brand on the bootstrap card', async () => {
    const { runtime } = fakeRuntime()
    const { container } = render(<App runtime={runtime} windowControls={fakeWindowControls()} />)

    expect(await screen.findByRole('heading', { name: '准备你的 DeepSeek Harness' })).toBeInTheDocument()
    expect(container.querySelector('svg[data-deepseek-fish-logo]')).toHaveAttribute('viewBox', '0 0 23.16 17.04')
  })

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
    expect(new URL(frame.getAttribute('src') ?? '').searchParams.get('dsh-desktop-parent-origin')).toBe(window.location.origin)
    // 官方 UI 的复制按钮依赖 clipboard-write 被委托进跨源 iframe。
    expect(frame).toHaveAttribute('allow', 'clipboard-write')
  })

  it('switches to the local app surface on launched and back on exited', async () => {
    const { runtime, emitLocalApp } = fakeRuntime()
    vi.mocked(runtime.bootstrapRuntime).mockResolvedValue({
      operationId: 'op-ready',
      phase: 'ready',
      rendererUrl: 'http://127.0.0.1:39000/?dsh-desktop-mode=advanced',
    })
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    await screen.findByTitle('DeepSeek Harness 工作台')

    act(() => emitLocalApp({ kind: 'launched', workspaceId: 'w-1', origin: 'http://127.0.0.1:39123', title: '记账应用' }))
    const appFrame = screen.getByTitle('本地应用 记账应用')
    expect(appFrame).toHaveAttribute('src', 'http://127.0.0.1:39123')
    expect(screen.getByText('正在运行：记账应用')).toBeVisible()
    expect(screen.getByTitle('DeepSeek Harness 工作台')).toHaveAttribute('data-hidden')

    act(() => emitLocalApp({ kind: 'exited', workspaceId: 'w-1', origin: 'http://127.0.0.1:39123', title: '记账应用' }))
    expect(screen.queryByTitle('本地应用 记账应用')).not.toBeInTheDocument()
    expect(screen.getByTitle('DeepSeek Harness 工作台')).not.toHaveAttribute('data-hidden')
  })

  it('ignores local app events with a non-loopback origin', async () => {
    const { runtime, emitLocalApp } = fakeRuntime()
    vi.mocked(runtime.bootstrapRuntime).mockResolvedValue({
      operationId: 'op-ready', phase: 'ready', rendererUrl: 'http://127.0.0.1:39000/',
    })
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    await screen.findByTitle('DeepSeek Harness 工作台')
    act(() => emitLocalApp({ kind: 'launched', workspaceId: 'w-1', origin: 'http://evil.example.com', title: 'x' }))
    expect(screen.queryByText('正在运行：x')).not.toBeInTheDocument()
  })

  it('clears the local app surface on stopped events', async () => {
    const { runtime, emitLocalApp } = fakeRuntime()
    vi.mocked(runtime.bootstrapRuntime).mockResolvedValue({
      operationId: 'op-ready', phase: 'ready', rendererUrl: 'http://127.0.0.1:39000/',
    })
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    await screen.findByTitle('DeepSeek Harness 工作台')
    act(() => emitLocalApp({ kind: 'launched', workspaceId: 'w-1', origin: 'http://127.0.0.1:39123', title: '记账应用' }))
    expect(screen.getByTitle('本地应用 记账应用')).toBeInTheDocument()
    act(() => emitLocalApp({ kind: 'stopped', workspaceId: 'w-1', origin: 'http://127.0.0.1:39123', title: '记账应用' }))
    expect(screen.queryByTitle('本地应用 记账应用')).not.toBeInTheDocument()
    expect(screen.getByTitle('DeepSeek Harness 工作台')).not.toHaveAttribute('data-hidden')
  })

  it('syncs only trusted workbench theme messages to the desktop chrome', async () => {
    const { runtime } = fakeRuntime()
    vi.mocked(runtime.bootstrapRuntime).mockResolvedValue({
      operationId: 'op-ready',
      phase: 'ready',
      rendererUrl: 'http://127.0.0.1:39000/',
    })
    const { container } = render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    const frame = await screen.findByTitle<HTMLIFrameElement>('DeepSeek Harness 工作台')
    const shell = container.querySelector('.windowShell')

    window.dispatchEvent(new MessageEvent('message', {
      data: { type: 'dsh-desktop-theme', colorScheme: 'light' },
      source: frame.contentWindow,
    }))
    await waitFor(() => expect(shell).toHaveAttribute('data-theme', 'light'))

    window.dispatchEvent(new MessageEvent('message', {
      data: { type: 'dsh-desktop-theme', colorScheme: 'dark' },
      source: window,
    }))
    expect(shell).toHaveAttribute('data-theme', 'light')
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

  it('shows real embedded extraction progress without a cancel action', async () => {
    const { runtime, emit } = fakeRuntime()
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    await waitFor(() => expect(runtime.bootstrapRuntime).toHaveBeenCalled())
    for (const completed of [0, 36, 100]) {
      emit({
        kind: 'progress',
        payload: {
          operationId: 'op-1', phase: 'extracting', completed, total: 100,
          message: `正在解压内置组件 ${completed}%`,
        },
      })
    }
    expect(await screen.findByText('正在解压内置组件 100%')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: '运行时准备进度' })).toHaveAttribute('aria-valuenow', '100')
    expect(screen.queryByRole('button', { name: '取消' })).not.toBeInTheDocument()

    emit({
      kind: 'progress',
      payload: {
        operationId: 'op-1', phase: 'verifying', completed: 100, total: 100,
        message: '正在验证组件',
      },
    })
    expect(await screen.findByText('正在验证组件')).toBeInTheDocument()
  })

  it('shows an indeterminate hint when no percentage is known', async () => {
    const { runtime, emit } = fakeRuntime()
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    await waitFor(() => expect(runtime.bootstrapRuntime).toHaveBeenCalled())
    emit({
      kind: 'progress',
      payload: { operationId: 'op-1', phase: 'checking', completed: 0, total: null, message: '正在检查 DeepSeek Harness…' },
    })
    expect(await screen.findByText('请稍候…')).toBeInTheDocument()
  })

  it('shows human failure copy with technical details behind the toggle', async () => {
    const { runtime, emit } = fakeRuntime()
    const { container } = render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    await waitFor(() => expect(runtime.bootstrapRuntime).toHaveBeenCalled())
    emit({
      kind: 'failure', operationId: 'op-1',
      payload: { code: 'process', message: 'Io error: entity not found (os error 2)', recoverable: true },
    })
    expect(await screen.findByRole('heading', { name: '启动遇到问题' })).toBeInTheDocument()
    expect(screen.getByText('DeepSeek Harness 意外退出，未能完成启动。请重试或修复。')).toBeInTheDocument()
    expect(screen.queryByText(/entity not found/)).not.toBeInTheDocument()
    expect(container.querySelector('.bootstrapCard')).toHaveAttribute('data-failed', 'true')
    fireEvent.click(screen.getByRole('button', { name: '查看详情' }))
    expect(screen.getByText('技术信息')).toBeInTheDocument()
    expect(screen.getByText(/entity not found/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '导出诊断' }))
    expect(await screen.findByText(/dsh-diagnostics\.zip/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '修复 DeepSeek Harness' })).toBeEnabled()
    fireEvent.click(screen.getByRole('button', { name: '修复 DeepSeek Harness' }))
    await waitFor(() => expect(runtime.repairRuntime).toHaveBeenCalled())
  })

  it('clears stale failure copy once a retry starts', async () => {
    const { runtime, emit } = fakeRuntime()
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    await waitFor(() => expect(runtime.bootstrapRuntime).toHaveBeenCalled())
    emit({
      kind: 'failure', operationId: 'op-1',
      payload: { code: 'process', message: 'Io error: entity not found (os error 2)', recoverable: true },
    })
    expect(await screen.findByText(/意外退出/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重试' }))
    expect(await screen.findByText('正在检查 DeepSeek Harness…')).toBeInTheDocument()
    expect(screen.queryByText(/意外退出/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '修复 DeepSeek Harness' })).not.toBeInTheDocument()
  })

  it('maps each failure code to plain-language copy', async () => {
    const cases: [RuntimeFailureCode, RegExp][] = [
      ['network', /网络连接不可用或太慢/],
      ['signature', /下载的文件未通过安全校验/],
      ['archive', /安装包似乎已损坏/],
      ['health-timeout', /启动等待超时/],
      ['cancelled', /本次启动已取消/],
      ['internal', /程序内部出现了一点问题/],
    ]
    for (const [code, pattern] of cases) {
      const { runtime, emit } = fakeRuntime()
      render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
      await waitFor(() => expect(runtime.bootstrapRuntime).toHaveBeenCalled())
      emit({ kind: 'failure', operationId: 'op-1', payload: { code, message: `raw ${code}`, recoverable: true } })
      expect(await screen.findByText(pattern)).toBeInTheDocument()
      cleanup()
    }
  })

  it('keeps the trusted title bar when the workbench becomes ready', async () => {
    const { runtime, emit } = fakeRuntime()
    render(<App runtime={runtime} windowControls={fakeWindowControls()} />)
    await waitFor(() => expect(runtime.bootstrapRuntime).toHaveBeenCalled())
    expect(screen.getByRole('button', { name: '关闭窗口' })).toBeInTheDocument()

    emit({
      kind: 'ready', operationId: 'op-1',
      rendererUrl: 'http://127.0.0.1:39000/?dsh-desktop-mode=advanced',
    })

    const frame = await screen.findByTitle('DeepSeek Harness 工作台')
    expect(frame).toHaveAttribute('src', expect.stringContaining('127.0.0.1:39000'))
    expect(screen.getByRole('button', { name: '关闭窗口' })).toBeInTheDocument()
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
    expect(screen.getByRole('button', { name: '关闭窗口' })).toBeInTheDocument()
  })
})
