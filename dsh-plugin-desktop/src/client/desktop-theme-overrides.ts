/**
 * Desktop-owned soft-dark palette. The vendored theme ships a near-black
 * `#151517` base with near-white `#f9fafb` labels (~16.5:1 contrast), which
 * reads as harsh next to developer-tool dark themes. This layer keeps the
 * light mode untouched and lowers the dark contrast into the ~11:1 range
 * (base `#1e1e21`, primary label `#d7dade`, solid borders instead of
 * translucent white hairlines), closer to terminal-assistant palettes.
 */

import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type {} from '@deepseek-ai/dsh-client-ui-theme/client'

/** Light values mirror the vendored theme so only dark changes. */
export function installDesktopSoftDarkTheme(ctx: ClientContext): () => void {
  // Older vendored theme builds (and test stubs) may not expose the layer
  // override API; the stock palette then stays in effect.
  if (typeof ctx.theme.overrideTokens !== 'function') return () => {}
  return ctx.theme.overrideTokens('dsh-plugin-desktop', {
    '--dsw-alias-bg-base': { light: 'var(--dsw-static-neutral-bluish-00)', dark: '#1e1e21' },
    '--dsw-alias-bg-layer-1': { light: 'var(--dsw-static-neutral-bluish-00)', dark: '#26262a' },
    '--dsw-alias-bg-layer-2': { light: 'var(--dsw-static-neutral-bluish-00)', dark: '#2e2e32' },
    '--dsw-alias-bg-layer-3': { light: 'var(--dsw-static-neutral-bluish-00)', dark: '#37373c' },
    '--dsw-specific-sidebar-fill': { light: 'var(--dsw-static-neutral-bluish-75)', dark: '#212124' },
    '--dsw-alias-border-l1': { light: '#0000000a', dark: '#2c2c31' },
    '--dsw-alias-border-l2': { light: '#0000001a', dark: '#35353b' },
    '--dsw-alias-label-primary': { light: 'var(--dsw-static-neutral-bluish-1000)', dark: '#d7dade' },
    '--dsw-alias-label-secondary': { light: 'var(--dsw-static-neutral-bluish-700)', dark: '#a6abb3' },
    '--dsw-alias-brand-primary': { light: 'var(--dsw-static-neutral-bluish-1000)', dark: '#e8eaed' },
  })
}