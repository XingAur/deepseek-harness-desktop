import { describe, expect, it } from 'vitest'
import { installAdvancedStyles } from '../src/client/styles'

describe('official UI style contract', () => {
  it('uses the upstream settings width and semantic tokens for the plugin market', () => {
    const dispose = installAdvancedStyles()
    const css = document.getElementById('dsh-desktop-advanced-styles')?.textContent ?? ''
    const pluginCss = css.slice(css.indexOf('.dshPluginMarket'), css.indexOf('.dshAgentPage'))

    expect(pluginCss).toContain('var(--dsw-alias-bg-layer-1')
    expect(pluginCss).toContain('var(--dsw-alias-label-secondary')
    expect(pluginCss).toContain('var(--dsh-desktop-divider)')
    dispose()
  })

  it('keeps desktop-owned settings sections inside the same upstream content geometry', () => {
    const dispose = installAdvancedStyles()
    const css = document.getElementById('dsh-desktop-advanced-styles')?.textContent ?? ''

    expect(css).toContain('.dshDesktopProfileSettings {')
    expect(css).toContain('.dshModelAgentCenter {')
    expect(css).toContain('.dshModelAgentTabs button[aria-selected="true"]')
    dispose()
  })

  it('renders the unified capability center as official token-based compact rows and dialogs', () => {
    const dispose = installAdvancedStyles()
    const css = document.getElementById('dsh-desktop-advanced-styles')?.textContent ?? ''
    const selectors = [
      '.dshCapabilityRow',
      '.dshCapabilityActions button',
      '.dshConnectionEditorGrid',
      '.dshConnectionTest',
      '.dshModelAgentDiagnosticRow',
      '.dshModelAgentDialogBackdrop',
      '.dshModelAgentDialog',
    ]
    const rules = selectors.map((selector) => {
      const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      return css.match(new RegExp(`${escaped} \\{[^}]*\\}`))?.[0] ?? ''
    })

    expect(rules.filter(Boolean).length).toBeGreaterThanOrEqual(3)
    expect(css).toContain('var(--dsw-alias-border-secondary')
    expect(css).toContain('var(--dsw-alias-bg-layer-2')
    dispose()
  })
})
