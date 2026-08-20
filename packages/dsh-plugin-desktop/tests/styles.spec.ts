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

  it('uses official surfaces with a quiet light divider and no market styles', () => {
    const dispose = installAdvancedStyles()
    const css = document.getElementById('dsh-desktop-advanced-styles')?.textContent ?? ''

    expect(css).toContain('var(--dsw-alias-bg-')
    expect(css).toContain('var(--dsw-alias-label-')
    expect(css).not.toMatch(/\.market[A-Z]|dshDesktopMarketEntry/)
    expect(css).toContain('body[data-dsh-desktop-mode="advanced"][data-dsh-desktop-theme="light"]')
    expect(css).toContain('--dsh-desktop-divider: rgba(29,38,58,.035)')
    expect(css).toContain('border-right: 1px solid var(--dsh-desktop-divider')

    dispose()
  })

  it('ships restrained project motion and reduced-motion overrides', () => {
    const dispose = installAdvancedStyles()
    const css = document.getElementById('dsh-desktop-advanced-styles')?.textContent ?? ''

    expect(css).toContain('.dshDesktopProjectCard:hover')
    expect(css).toContain('transform: translateY(-2px)')
    expect(css).toContain('.dshDesktopProjectCard[data-recent="true"]::after')
    expect(css).toContain('@media (prefers-reduced-motion: reduce)')
    expect(css).toContain('animation: none')

    dispose()
  })
})
