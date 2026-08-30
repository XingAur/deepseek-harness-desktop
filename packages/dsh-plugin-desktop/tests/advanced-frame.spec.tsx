import { act, fireEvent, render, screen } from '@testing-library/react'
import { useSyncExternalStore } from 'react'
import { describe, expect, it, vi } from 'vitest'
import { AdvancedFrame } from '../src/client/AdvancedFrame'
import { ExtensionCenterFooterAction } from '../src/client/ExtensionCenterFooterAction'
import { LocalProjectsFooterAction } from '../src/client/LocalProjectsFooterAction'
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

  it('matches official footer geometry in wide and rail states', () => {
    const state = new LocalProjectsState()
    const { rerender } = render(<LocalProjectsFooterAction wide state={state} />)
    let entry = screen.getByRole('button', { name: '本地项目' })
    expect(entry).toBeVisible()
    expect(entry.querySelector('svg')).toBeInTheDocument()
    expect(entry).toHaveClass('dshDesktopFooterAction')
    expect(screen.getByText('本地项目')).toHaveClass('dshDesktopFooterActionLabel')

    rerender(<LocalProjectsFooterAction wide={false} state={state} />)
    entry = screen.getByRole('button', { name: '本地项目' })
    expect(entry).toHaveClass('dshDesktopFooterAction', 'is-rail')
    expect(screen.queryByText('本地项目')).not.toBeInTheDocument()
    fireEvent.click(entry)
    expect(state.getSnapshot()).toBe(true)
  })

  it('侧边栏不再渲染 Agent 按钮', () => {
    renderFrame()
    expect(screen.queryByRole('button', { name: 'Agent' })).not.toBeInTheDocument()
  })

  it('扩展中心按钮开合对话面', () => {
    const state = new ExtensionCenterState()
    renderFrame({ extensionCenter: state })
    expect(screen.queryByRole('complementary', { name: '扩展中心' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '扩展中心' }))
    expect(state.getSnapshot()).toBe(true)
    expect(screen.getByRole('complementary', { name: '扩展中心' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '提示词' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByText('MCP')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: 'MCP' }))
    expect(screen.getByText('即将推出')).toBeInTheDocument()

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

  it('footer 顺序为 本地项目 → 扩展中心(设置由官方渲染在其后)', () => {
    renderFrame()
    const labels = Array.from(document.querySelectorAll('.dshDesktopFooterActionLabel'))
      .map((node) => node.textContent)
    expect(labels).toContain('本地项目')
    expect(labels).toContain('扩展中心')
    expect(labels.indexOf('本地项目')).toBeLessThan(labels.indexOf('扩展中心'))
  })

  it('matches the extension center footer geometry in wide and rail states', () => {
    const state = new ExtensionCenterState()
    const { rerender } = render(<ExtensionCenterFooterAction wide state={state} />)
    let entry = screen.getByRole('button', { name: '扩展中心' })
    expect(entry).toBeVisible()
    expect(entry.querySelector('svg')).toBeInTheDocument()
    expect(entry).toHaveClass('dshDesktopFooterAction')
    expect(screen.getByText('扩展中心')).toHaveClass('dshDesktopFooterActionLabel')

    rerender(<ExtensionCenterFooterAction wide={false} state={state} />)
    entry = screen.getByRole('button', { name: '扩展中心' })
    expect(entry).toHaveClass('dshDesktopFooterAction', 'is-rail')
    expect(screen.queryByText('扩展中心')).not.toBeInTheDocument()
    fireEvent.click(entry)
    expect(state.getSnapshot()).toBe(true)
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
