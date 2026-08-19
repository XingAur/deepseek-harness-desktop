import { render } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AdvancedFrame } from '../src/client/AdvancedFrame'
import { DesktopLayoutState } from '../src/client/layout-state'

class ResizeObserverStub {
  observe() {}
  disconnect() {}
}

vi.stubGlobal('ResizeObserver', ResizeObserverStub)

describe('advanced frame', () => {
  for (const platform of ['win32', 'darwin'] as const) {
    it(`does not reserve an inner ${platform} caption row`, () => {
      const { container } = render(
        <AdvancedFrame
          layout={new DesktopLayoutState()}
          platform={platform}
          renderSlot={(name) => <div data-slot={name} />}
          useSessions={(selector) => selector({ byId: {} })}
        />,
      )

      expect(container.querySelector('.dshDesktopWindowsCaptionRow')).toBeNull()
      expect(container.querySelector('.dshDesktopMacCaptionRow')).toBeNull()
      expect(container.querySelector('[data-slot="sidebar"]')).toBeInTheDocument()
      expect(container.querySelector('[data-slot="conversation"]')).toBeInTheDocument()
    })
  }
})
