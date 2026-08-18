import type { ClientContextLike } from './contracts'
import type { DesktopLayoutState } from './layout-state'

export function provideDesktopLayout(ctx: ClientContextLike, layout: DesktopLayoutState): () => void {
  const extended = ctx as ClientContextLike & { desktopLayout?: DesktopLayoutState }
  extended.desktopLayout = layout
  return () => { if (extended.desktopLayout === layout) delete extended.desktopLayout }
}
