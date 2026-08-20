import { describe, expect, it, vi } from 'vitest'
import { applyAdvancedShell } from '../src/client/advanced-shell'
import type { ClientContextLike } from '../src/client/contracts'

describe('advanced shell', () => {
  it('registers the profile manager through the official settings section and disposes it', () => {
    const disposeSection = vi.fn()
    const register = vi.fn((definition) => definition.name === 'settings.section' ? disposeSection : () => undefined)
    const callbacks: Array<() => void | (() => void)> = []
    const inject = vi.fn((_name: string, setup: () => void | (() => void)) => {
      callbacks.push(setup)
      return () => undefined
    })
    const context = {
      effect: (setup: () => void | (() => void)) => { setup() },
      reflect: { provide: vi.fn(() => () => undefined) },
      slots: { register, inject },
      workspaces: { list: {} }, sessions: { list: {} },
    } as unknown as ClientContextLike

    applyAdvancedShell(context, 'win32')

    expect(inject).toHaveBeenCalledWith('settings.section', expect.any(Function))
    const dispose = callbacks[0]?.()
    expect(register).toHaveBeenCalledWith(expect.objectContaining({
      name: 'settings.section', id: 'dsh-desktop-profiles', order: 60, label: 'Profiles',
    }), expect.any(Function))
    dispose?.()
    expect(disposeSection).toHaveBeenCalledOnce()
  })

  it('passes the official workspace and session services into the desktop frame', () => {
    const workspaces = { list: {} }
    const sessions = { list: {} }
    let registeredRoot: { inject(): Record<string, unknown> } | undefined
    const context = {
      effect: (setup: () => void | (() => void)) => { setup() },
      reflect: { provide: vi.fn(() => () => undefined) },
      slots: {
        register: vi.fn((definition) => {
          registeredRoot = definition
          return () => undefined
        }),
      },
      workspaces,
      sessions,
    } as unknown as ClientContextLike

    applyAdvancedShell(context, 'win32')

    expect(registeredRoot?.inject()).toMatchObject({ workspaces, sessions })
  })

  it('does not assign undeclared services onto the host context', () => {
    const register = vi.fn(() => () => undefined)
    const provide = vi.fn(() => () => undefined)
    const rawContext = {
      effect: (setup: () => void | (() => void)) => { setup() },
      reflect: { provide },
      slots: { register },
      workspaces: { list: {} },
      sessions: { list: {} },
    } as unknown as ClientContextLike & { reflect: { provide(name: string, value: unknown): () => void } }
    const context = new Proxy(rawContext, {
      set(target, property, value, receiver) {
        if (!Reflect.has(target, property)) {
          throw new Error(`cannot set property "${String(property)}" without provide`)
        }
        return Reflect.set(target, property, value, receiver)
      },
    })

    expect(() => applyAdvancedShell(context, 'win32')).not.toThrow()
    expect(provide).toHaveBeenCalledWith('layout', expect.any(Object))
    expect(register).toHaveBeenCalledWith(
      expect.objectContaining({ name: 'root' }),
      expect.any(Function),
    )
  })
})
