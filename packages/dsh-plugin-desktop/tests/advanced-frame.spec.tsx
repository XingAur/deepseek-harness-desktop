import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { useSyncExternalStore } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { AdvancedFrame } from '../src/client/AdvancedFrame'
import { SidebarFooterActions } from '../src/client/SidebarFooterActions'
import { bridgeFixture, renderFrame, sessionFixture, workspaceFixture } from './fixtures'
import { DesktopLayoutState } from '../src/client/layout-state'
import { ExtensionCenterState } from '../src/client/extension-center-state'
import { LocalProjectsState } from '../src/client/local-projects-state'

class ResizeObserverStub {
  observe() {}
  disconnect() {}
}

vi.stubGlobal('ResizeObserver', ResizeObserverStub)

describe('advanced frame', () => {
  it('opens the card grid from the shared local-projects state without appending a sidebar button', async () => {
    const workspaces = workspaceFixture([{
      workspaceId: 'w-1', path: 'C:\\code\\demo', title: 'demo', sessionIds: [],
      createdAt: '2026-08-19T00:00:00Z', updatedAt: '2026-08-19T00:00:00Z',
    }])
    const localProjects = new LocalProjectsState()
    const { container } = renderFrame({ workspaces, localProjects })

    expect(container.querySelector('.dshDesktopProjectsEntry')).toBeNull()
    act(() => localProjects.open())
    expect(screen.getByRole('region', { name: '本地项目' })).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '项目 demo' })).toBeInTheDocument()
    expect(screen.queryByText('社区插件')).not.toBeInTheDocument()
  })

  it('侧边栏 footer 功能按钮合并为单列组并匹配官方几何', () => {
    const localProjects = new LocalProjectsState()
    const extensionCenter = new ExtensionCenterState()
    const { rerender } = render(
      <SidebarFooterActions wide localProjects={localProjects} extensionCenter={extensionCenter} />,
    )
    let group = screen.getByRole('group', { name: '桌面功能' })
    expect(group).toHaveClass('dshDesktopFooterActions')
    const buttonLabels = within(group).getAllByRole('button').map((button) => button.getAttribute('aria-label'))
    expect(buttonLabels).toEqual(['本地项目', '扩展中心'])
    for (const label of ['本地项目', '扩展中心']) {
      const entry = within(group).getByRole('button', { name: label })
      expect(entry.querySelector('svg')).toBeInTheDocument()
      expect(within(entry).getByText(label)).toHaveClass('dshDesktopFooterActionLabel')
    }

    rerender(<SidebarFooterActions wide={false} localProjects={localProjects} extensionCenter={extensionCenter} />)
    group = screen.getByRole('group', { name: '桌面功能' })
    expect(group).toHaveClass('dshDesktopFooterActions', 'is-rail')
    for (const label of ['本地项目', '扩展中心']) {
      const entry = within(group).getByRole('button', { name: label })
      expect(entry).toHaveClass('dshDesktopFooterAction', 'is-rail')
      expect(screen.queryByText(label)).not.toBeInTheDocument()
    }
    fireEvent.click(within(group).getByRole('button', { name: '本地项目' }))
    expect(localProjects.getSnapshot()).toBe(true)
    fireEvent.click(within(group).getByRole('button', { name: '扩展中心' }))
    expect(extensionCenter.getSnapshot()).toBe(true)
  })

  it('侧边栏不再渲染被替换的插件入口按钮', () => {
    renderFrame()
    expect(screen.queryByRole('button', { name: '插件' })).not.toBeInTheDocument()
  })

  it('扩展中心按钮开合对话面', async () => {
    const state = new ExtensionCenterState()
    // MCP 页签已落地为同步面板:桥接夹具需提供列表/状态应答,避免面板渲染崩溃。
    const bridge = bridgeFixture()
    ;(bridge.requestV2 as ReturnType<typeof vi.fn>).mockImplementation((action: string) =>
      action === 'mcp.list' ? [] : action === 'mcp.status' ? [] : undefined)
    const { container } = renderFrame({ extensionCenter: state, bridge })
    expect(screen.queryByRole('complementary', { name: '扩展中心' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '扩展中心' }))
    expect(state.getSnapshot()).toBe(true)
    expect(screen.getByRole('complementary', { name: '扩展中心' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '提示词' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: '插件' })).toBeInTheDocument()
    expect(screen.getByText('MCP')).toBeInTheDocument()
    // 提示词面板被错误边界包裹：正常渲染时不应出现降级错误卡
    expect(screen.queryAllByRole('alert')).toHaveLength(0)
    expect(container.querySelector('.dshExtErrorCard')).toBeNull()

    // 插件页签同样被边界包裹：内容正常渲染而非降级错误卡
    fireEvent.click(screen.getByRole('tab', { name: '插件' }))
    expect(await screen.findByRole('region', { name: '插件市场' })).toBeInTheDocument()
    expect(screen.queryByText('插件 暂时不可用')).not.toBeInTheDocument()
    expect(container.querySelector('.dshExtErrorCard')).toBeNull()

    fireEvent.click(screen.getByRole('tab', { name: 'MCP' }))
    expect(await screen.findByRole('button', { name: '添加服务器' })).toBeInTheDocument()
    expect(container.querySelector('.dshExtErrorCard')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: '扩展中心' }))
    expect(state.getSnapshot()).toBe(false)
    expect(screen.queryByRole('complementary', { name: '扩展中心' })).not.toBeInTheDocument()
    expect(screen.getByTestId('conversation-slot')).toBeInTheDocument()
  })

  it('扩展中心与本地项目互斥并占用对话面', () => {
    const localProjects = new LocalProjectsState()
    const extensionCenter = new ExtensionCenterState()
    renderFrame({ localProjects, extensionCenter })

    act(() => localProjects.open())
    expect(screen.getByRole('region', { name: '本地项目' })).toBeInTheDocument()
    act(() => extensionCenter.open())
    expect(localProjects.getSnapshot()).toBe(false)
    expect(screen.getByRole('complementary', { name: '扩展中心' })).toBeInTheDocument()
    expect(screen.queryByTestId('conversation-slot')).toBeNull()

    act(() => localProjects.open())
    expect(extensionCenter.getSnapshot()).toBe(false)
    expect(screen.getByRole('region', { name: '本地项目' })).toBeInTheDocument()
  })

  it('footer 的本地项目与扩展中心都渲染在单列组容器内', () => {
    renderFrame()
    const group = document.querySelector('.dshDesktopFooterActions')
    expect(group).not.toBeNull()
    const labels = Array.from(group?.querySelectorAll('.dshDesktopFooterActionLabel') ?? [])
      .map((node) => node.textContent)
    expect(labels).toContain('本地项目')
    expect(labels).toContain('扩展中心')
    expect(document.querySelectorAll('.dshDesktopFooterAction')).toHaveLength(2)
  })

  for (const platform of ['win32', 'darwin'] as const) {
    it(`does not reserve an inner ${platform} caption row`, () => {
      const { container } = render(
        <AdvancedFrame
          layout={new DesktopLayoutState()}
          platform={platform}
          renderSlot={(name) => <div data-slot={name} />}
          useSessions={(selector) => selector({ byId: {} })}
          useWorkspaces={(selector) => selector(workspaceFixture().list.getSnapshot())}
          workspaces={workspaceFixture()}
          sessions={sessionFixture()}
          bridge={bridgeFixture()}
          localProjects={new LocalProjectsState()}
          extensionCenter={new ExtensionCenterState()}
        />,
      )

      expect(container.querySelector('.dshDesktopWindowsCaptionRow')).toBeNull()
      expect(container.querySelector('.dshDesktopMacCaptionRow')).toBeNull()
      expect(container.querySelector('[data-slot="sidebar"]')).toBeInTheDocument()
      expect(container.querySelector('[data-slot="conversation"]')).toBeInTheDocument()
    })
  }

  it('renders only the upstream sidebar and conversation surfaces', () => {
    render(
      <AdvancedFrame
        layout={new DesktopLayoutState()}
        platform="win32"
        renderSlot={(name) => <div data-testid={`${name}-slot`} />}
        useSessions={(selector) => selector({ byId: {} })}
        useWorkspaces={(selector) => selector(workspaceFixture().list.getSnapshot())}
        workspaces={workspaceFixture()}
        sessions={sessionFixture()}
        bridge={bridgeFixture()}
        localProjects={new LocalProjectsState()}
        extensionCenter={new ExtensionCenterState()}
      />,
    )

    expect(screen.queryByRole('button', { name: '社区插件' })).toBeNull()
    expect(screen.getByTestId('sidebar-slot')).toBeInTheDocument()
    expect(screen.getByTestId('conversation-slot')).toBeInTheDocument()
  })

  it('returns to the conversation when a session becomes current', () => {
    const sessions = sessionFixture()
    const localProjects = new LocalProjectsState()
    const extensionCenter = new ExtensionCenterState()
    renderFrame({
      sessions,
      localProjects,
      extensionCenter,
      useSessions: (selector) => useSyncExternalStore(
        sessions.list.subscribe,
        () => selector(sessions.list.getSnapshot()),
      ),
    })

    act(() => localProjects.open())
    expect(screen.getByRole('region', { name: '本地项目' })).toBeInTheDocument()
    expect(screen.queryByTestId('conversation-slot')).toBeNull()

    act(() => sessions.setCurrent('s-new'))
    expect(localProjects.getSnapshot()).toBe(false)
    expect(screen.queryByRole('region', { name: '本地项目' })).toBeNull()
    expect(screen.getByTestId('conversation-slot')).toBeInTheDocument()

    act(() => extensionCenter.open())
    expect(screen.queryByTestId('conversation-slot')).toBeNull()
    act(() => sessions.setCurrent('s-another'))
    expect(extensionCenter.getSnapshot()).toBe(false)
    expect(screen.getByTestId('conversation-slot')).toBeInTheDocument()
  })
})
