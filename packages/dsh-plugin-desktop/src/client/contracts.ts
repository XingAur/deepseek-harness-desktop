import type { ComponentType, ReactNode } from 'react'

export type DesktopPlatform = 'win32' | 'darwin'

export interface SlotDefinition {
  name: string
  children: Record<string, { kind: 'single' | 'list'; scope: 'root' | 'session' | 'session-maybe' }>
  inject: () => Record<string, unknown>
}

export interface ClientContextLike {
  effect(register: () => void | (() => void), label: string): void
  on?(event: 'theme/change', listener: (snapshot: unknown) => void): () => void
  reflect: { provide(name: string, value: unknown): () => void }
  slots: { register(definition: SlotDefinition, component: ComponentType<any>): () => void }
  theme?: {
    getTheme(): unknown
  }
}

export interface AdvancedFrameProps {
  layout: import('./layout-state').DesktopLayoutState
  platform: DesktopPlatform
  renderSlot(name: 'sidebar' | 'conversation' | 'details' | 'shell.overlay', props: Record<string, unknown>): ReactNode
  useSessions<T>(selector: (state: { current?: string; byId: Record<string, { blank?: boolean }> }) => T): T
}
