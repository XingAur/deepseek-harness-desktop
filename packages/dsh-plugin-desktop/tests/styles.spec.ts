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

  it('keeps the local-project entry in the neutral sidebar color family', () => {
    const dispose = installAdvancedStyles()
    const css = document.getElementById('dsh-desktop-advanced-styles')?.textContent ?? ''

    expect(css).toMatch(
      /\.dshDesktopSidebarSurface \{[^}]*background: var\(--dsw-specific-sidebar-fill/,
    )
    expect(css).not.toMatch(
      /\[data-desktop-platform="darwin"\] \.dshDesktopSidebarSurface \{[^}]*background:/,
    )
    expect(css).toMatch(/\.dshDesktopFooterAction \{[^}]*color: var\(--dsw-alias-label-primary/)
    expect(css).toMatch(/\.dshDesktopFooterAction \{[^}]*background: transparent/)
    expect(css).toMatch(/\.dshDesktopFooterAction:hover \{[^}]*background: var\(--dsw-alias-interactive-bg-hover/)
    expect(css).toMatch(/\.dshDesktopFooterAction \{[^}]*height: 42px/)
    expect(css).toMatch(/\.dshDesktopFooterAction\.is-rail \{[^}]*width: 36px;[^}]*height: 36px/)
    expect(css).toMatch(/\.dshDesktopFooterAction\.is-active \{[^}]*background: var\(--dsw-specific-sidebar-nav-item-active/)
    expect(css).not.toMatch(/\.dshDesktopFooterAction\.is-active[^}]*#6482dc/)
    expect(css).not.toMatch(/\.dshDesktopFooterAction\.is-active[^}]*border-color:/)

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

  it('keeps plugin surfaces on real theme tokens with light-neutral fallbacks', () => {
    const dispose = installAdvancedStyles()
    const css = document.getElementById('dsh-desktop-advanced-styles')?.textContent ?? ''

    // 官方主题不存在这些变量名；引用它们会让深色回退恒生效（弹窗恒深色）。
    expect(css).not.toContain('--dsw-alias-surface-primary')
    expect(css).not.toContain('--dsw-alias-surface-secondary')
    expect(css).not.toContain('--dsw-alias-border-primary')
    expect(css).not.toContain('--dsw-alias-border-strong')
    expect(css).not.toContain('--dsw-alias-border-l2')

    // 导入现有提示词弹窗必须走与其它弹窗一致的官方表层变量。
    expect(css).toMatch(/\.dshPromptsDialog \{[^}]*background: var\(--dsw-alias-bg-layer-1, #ffffff\)/)
    expect(css).toMatch(/\.dshPromptsModeSwitch button\[aria-pressed='true'\] \{[^}]*var\(--dsw-alias-interactive-bg-hover/)
    expect(css).toMatch(/\.dshPromptsListItem\.is-active \{[^}]*var\(--dsw-alias-interactive-bg-hover/)

    // 回退值一律浅色/中性，杜绝变量缺失时浅色主题渲染成深色面板。
    expect(css).not.toMatch(/var\(--[\w-]+, #(1c1c1f|1d1d20|29292e|2c2c2f|151517|141416|101013)\)/)
    expect(css).not.toMatch(/var\(--dsw-alias-label-\w+, #(b7b7bf|85858d|ececf0|f4f4f5|e8edf2|f0f0f3)\)/)
    expect(css).toMatch(/\.dshPluginJobLog \{[^}]*background: var\(--dsw-alias-bg-base, #ffffff\)/)
    expect(css).toMatch(/\.dshAgentLog \{[^}]*background: var\(--dsw-alias-bg-base, #ffffff\)/)

    // 浅色主题下压暗仅按深色设计的高亮硬编码文字。
    expect(css).toContain('body[data-dsh-desktop-theme="light"] .dshPluginMarketCategories button.is-active')
    expect(css).toContain('body[data-dsh-desktop-theme="light"] .dshAgentStep[data-state="active"] .dshAgentStepMark')
    expect(css).toContain('body[data-dsh-desktop-theme="light"] .dshAgentWorkbenchDiff pre')

    dispose()
  })
})
