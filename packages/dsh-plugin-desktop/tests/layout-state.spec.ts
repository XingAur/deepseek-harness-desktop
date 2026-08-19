import { describe, expect, it } from 'vitest'
import { computeDesktopColumns, DesktopLayoutState, SIDEBAR_COLLAPSED } from '../src/client/layout-state'

describe('desktop layout', () => {
  it('keeps a usable conversation width', () => {
    expect(computeDesktopColumns(900, 380, 420, SIDEBAR_COLLAPSED)).toEqual({ sidebar: 380, details: 0 })
  })
  it('notifies subscribers only on changes', () => {
    const layout = new DesktopLayoutState()
    let calls = 0
    layout.subscribe(() => calls++)
    layout.setSidebar(400)
    layout.setSidebar(400)
    expect(calls).toBe(1)
  })
})
