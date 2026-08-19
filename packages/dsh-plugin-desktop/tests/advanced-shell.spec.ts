import { describe, expect, it, vi } from 'vitest'
import { applyAdvancedShell } from '../src/client/advanced-shell'
import type { ClientContextLike } from '../src/client/contracts'

describe('advanced shell', () => {
  it('does not assign undeclared services onto the host context', () => {
    const register = vi.fn(() => () => undefined)
    const provide = vi.fn(() => () => undefined)
    const rawContext = {
      effect: (setup) => { setup() },
      reflect: { provide },
      slots: { register },
    } as ClientContextLike & { reflect: { provide(name: string, value: unknown): () => void } }
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
