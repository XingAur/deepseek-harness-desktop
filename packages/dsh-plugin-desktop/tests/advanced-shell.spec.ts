import { describe, expect, it, vi } from 'vitest'
import { applyAdvancedShell } from '../src/client/advanced-shell'
import type { ClientContextLike } from '../src/client/contracts'
import { sessionFixture, workspaceFixture } from './fixtures'

describe('advanced shell', () => {
  it('installs and disposes the new-session transition with the shell generation', () => {
    const workspaces = workspaceFixture()
    const sessions = sessionFixture()
    const original = workspaces.startSession
    let disposeTransition: (() => void) | undefined
    const context = {
      effect: (setup: () => void | (() => void), label: string) => {
        const dispose = setup()
        if (label === 'desktop: new session transition' && typeof dispose === 'function') {
          disposeTransition = dispose
        }
      },
      reflect: { provide: vi.fn(() => () => undefined) },
      slots: { register: vi.fn(() => () => undefined) },
      workspaces,
      sessions,
    } as unknown as ClientContextLike

    applyAdvancedShell(context, 'win32')

    expect(workspaces.startSession).not.toBe(original)
    disposeTransition?.()
    expect(workspaces.startSession).toBe(original)
  })

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

  it('does not read an undeclared llm service while preparing the root slot', () => {
    let registeredRoot: { inject(): Record<string, unknown> } | undefined
    const provide = vi.fn(() => () => undefined)
    const rawContext = {
      effect: (setup: () => void | (() => void)) => { setup() },
      reflect: { provide },
      slots: {
        register: vi.fn((definition) => {
          if (definition.name === 'root') registeredRoot = definition
          return () => undefined
        }),
      },
      workspaces: { list: {} },
      sessions: { list: {} },
    } as unknown as ClientContextLike
    const context = new Proxy(rawContext, {
      get(target, property, receiver) {
        if (property === 'llm') throw new Error('cannot get property llm without inject')
        return Reflect.get(target, property, receiver)
      },
    })

    applyAdvancedShell(context, 'win32')

    expect(() => registeredRoot?.inject()).not.toThrow()
    expect(provide).toHaveBeenCalledWith('layout', expect.any(Object))
    expect(registeredRoot?.inject()).toEqual(expect.objectContaining({
      workspaces: rawContext.workspaces,
      sessions: rawContext.sessions,
    }))
    expect(registeredRoot?.inject()).not.toHaveProperty('modelId')
  })

  it('registers local projects in the official sidebar footer action slot', () => {
    const registrations: Array<{ definition: any; component: unknown }> = []
    const callbacks: Array<{ name: string; setup: () => void | (() => void) }> = []
    const context = {
      effect: (setup: () => void | (() => void)) => { setup() },
      reflect: { provide: vi.fn(() => () => undefined) },
      slots: {
        register: vi.fn((definition, component) => {
          registrations.push({ definition, component })
          return () => undefined
        }),
        inject: vi.fn((name: string, setup: () => void | (() => void)) => {
          callbacks.push({ name, setup })
          return () => undefined
        }),
      },
      workspaces: { list: {} },
      sessions: { list: {} },
    } as unknown as ClientContextLike

    applyAdvancedShell(context, 'win32')
    const footer = callbacks.find((callback) => callback.name === 'sidebar.footer.action')
    expect(footer).toBeDefined()
    footer?.setup()
    const action = registrations.find(({ definition }) => definition.name === 'sidebar.footer.action')
    const root = registrations.find(({ definition }) => definition.name === 'root')
    expect(action?.definition).toMatchObject({ id: 'dsh-desktop-local-projects', order: 10 })
    expect(action?.definition.inject().state).toBe(root?.definition.inject().localProjects)
  })

  it('orders the sidebar footer actions as 本地项目 → 扩展中心 (settings renders after them)', () => {
    const registrations: Array<{ definition: any; component: unknown }> = []
    const callbacks: Array<{ name: string; setup: () => void | (() => void) }> = []
    const context = {
      effect: (setup: () => void | (() => void)) => { setup() },
      reflect: { provide: vi.fn(() => () => undefined) },
      slots: {
        register: vi.fn((definition, component) => {
          registrations.push({ definition, component })
          return () => undefined
        }),
        inject: vi.fn((name: string, setup: () => void | (() => void)) => {
          callbacks.push({ name, setup })
          return () => undefined
        }),
      },
      workspaces: { list: {} },
      sessions: { list: {} },
    } as unknown as ClientContextLike

    applyAdvancedShell(context, 'win32')
    callbacks.filter((callback) => callback.name === 'sidebar.footer.action')
      .forEach((callback) => callback.setup())
    const actions = registrations
      .filter(({ definition }) => definition.name === 'sidebar.footer.action')
      .sort((left, right) => left.definition.order - right.definition.order)

    expect(actions.map(({ definition }) => definition.id)).toEqual([
      'dsh-desktop-local-projects',
      'dsh-desktop-extension-center',
    ])
    expect(actions.map(({ definition }) => definition.order)).toEqual([10, 20])
    expect(actions[1]?.definition.inject().state)
      .toBe(registrations.find(({ definition }) => definition.name === 'root')?.definition.inject().extensionCenter)
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
