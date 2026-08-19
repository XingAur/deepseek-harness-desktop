import type { ClientContextLike } from './contracts'
import type { DesktopLayoutState } from './layout-state'

export function provideDesktopLayout(ctx: ClientContextLike, layout: DesktopLayoutState): () => void {
  return ctx.reflect.provide('layout', layout)
}
