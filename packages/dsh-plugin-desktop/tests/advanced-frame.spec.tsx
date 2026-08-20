import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AdvancedFrame } from '../src/client/AdvancedFrame'
import { bridgeFixture, renderFrame, sessionFixture, workspaceFixture } from './fixtures'
import { DesktopLayoutState } from '../src/client/layout-state'

class ResizeObserverStub {
  observe() {}
  disconnect() {}
}

vi.stubGlobal('ResizeObserver', ResizeObserverStub)

describe('advanced frame', () => {
  it('reuses the cube entry as local projects and opens the card grid', () => {
    const workspaces = workspaceFixture([{
      workspaceId: 'w-1', path: 'C:\\code\\demo', title: 'demo', sessionIds: [],
      createdAt: '2026-08-19T00:00:00Z', updatedAt: '2026-08-19T00:00:00Z',
    }])
    renderFrame({ workspaces })

    const entry = screen.getByRole('button', { name: '本地项目' })
    expect(entry.querySelector('svg')).toHaveAttribute('aria-hidden', 'true')
    fireEvent.click(entry)
    expect(screen.getByRole('heading', { name: '本地项目' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '项目 demo' })).toBeInTheDocument()
    expect(screen.queryByText('社区插件')).not.toBeInTheDocument()
  })

  it('keeps the local projects cube available when the sidebar is collapsed', () => {
    const layout = new DesktopLayoutState()
    layout.setSidebar(0)
    renderFrame({ layout })

    const entry = screen.getByRole('button', { name: '本地项目' })
    expect(entry).toBeVisible()
    expect(entry.querySelector('svg')).toBeInTheDocument()
    expect(entry.querySelector('span')).not.toBeInTheDocument()
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
      />,
    )

    expect(screen.queryByRole('button', { name: '社区插件' })).toBeNull()
    expect(screen.getByTestId('sidebar-slot')).toBeInTheDocument()
    expect(screen.getByTestId('conversation-slot')).toBeInTheDocument()
  })
})
