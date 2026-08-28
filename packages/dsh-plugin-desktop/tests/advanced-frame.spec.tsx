import { act, fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AdvancedFrame } from '../src/client/AdvancedFrame'
import { LocalProjectsFooterAction } from '../src/client/LocalProjectsFooterAction'
import { bridgeFixture, renderFrame, sessionFixture, workspaceFixture } from './fixtures'
import { DesktopLayoutState } from '../src/client/layout-state'
import { LocalProjectsState } from '../src/client/local-projects-state'
import { PluginCenterState } from '../src/client/plugin-center-state'

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
          pluginCenter={new PluginCenterState()}
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
        pluginCenter={new PluginCenterState()}
      />,
    )

    expect(screen.queryByRole('button', { name: '社区插件' })).toBeNull()
    expect(screen.getByTestId('sidebar-slot')).toBeInTheDocument()
    expect(screen.getByTestId('conversation-slot')).toBeInTheDocument()
  })
})
