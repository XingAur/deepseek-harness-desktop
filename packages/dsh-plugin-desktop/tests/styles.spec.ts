import { describe, expect, it } from 'vitest'
import { installAdvancedStyles } from '../src/client/styles'

describe('advanced styles', () => {
  it('starts product surfaces at the top of the iframe', () => {
    const dispose = installAdvancedStyles()
    const css = document.getElementById('dsh-desktop-advanced-styles')?.textContent ?? ''

    expect(css).not.toContain('CaptionRow')
    expect(css).not.toMatch(/padding-top:\s*(48|58)px/)
    expect(css).toContain('.dshDesktopResizeHandle { position: absolute; top: 0;')

    dispose()
  })
})
