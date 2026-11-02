/** Per-model-call transport routing. Never modifies env, fetch, or WebSocket. */
import { AsyncLocalStorage } from 'node:async_hooks'
import { createRequire } from 'node:module'
import type { Agent, Dispatcher, ProxyAgent } from 'undici'
import { createModelProxySnapshot, resolveModelProxyRoute } from './model-proxy-routing.ts'
import type { ModelProxySnapshot } from './model-proxy-routing.ts'
import { installModelProxyNodeGuards, ModelProxyNodeAgents, ModelProxyTransportError } from './model-proxy-node.ts'
import type { ModelProxyNodeScope } from './model-proxy-node.ts'

export { ModelProxyTransportError } from './model-proxy-node.ts'
export type { ModelProxySnapshot } from './model-proxy-routing.ts'

export interface ModelProxyRuntimeOptions {
  /** Primarily useful for local fixtures/custom trusted roots; never env-derived. */
  readonly agentOptions?: Agent.Options
  readonly proxyAgentOptions?: Omit<ProxyAgent.Options, 'uri'>
}

type Undici = typeof import('undici')
type Dispatch = Dispatcher['dispatch']
interface Generation {
  readonly snapshot: ModelProxySnapshot
  readonly direct: Dispatcher
  readonly proxies: Map<string, Dispatcher>
  readonly native: ModelProxyNodeAgents
  references: number
  retired: boolean
  closing: boolean
}
interface Scope extends ModelProxyNodeScope {
  readonly owner: ModelProxyRuntime
  readonly generation: Generation
  readonly order: number
  released: boolean
  dispatch(options: Parameters<Dispatch>[0], handler: Parameters<Dispatch>[1]): boolean
}
interface Hub {
  readonly undici: Undici
  readonly instances: Set<WeakRef<ModelProxyRuntime>>
  readonly modern: Dispatcher
  readonly legacy: Dispatcher
  order: number
}

const HUB = Symbol.for('dsh.overlay.model-proxy-runtime.v1')
const MODERN = Symbol.for('undici.globalDispatcher.2')
const LEGACY = Symbol.for('undici.globalDispatcher.1')
const require = createRequire(import.meta.url)
const globals = globalThis as typeof globalThis & Record<symbol, any>

function transparentDispatcher(previous: Dispatcher, dispatch: Dispatch): Dispatcher {
  // Keep the original receiver for custom/private fields and all non-dispatch
  // methods. Do not close/destroy the pre-installation dispatcher ourselves.
  return new Proxy(previous, {
    get(target, property) {
      if (property === 'dispatch') return dispatch
      const value = Reflect.get(target, property, target)
      return typeof value === 'function' ? value.bind(target) : value
    },
  })
}

function ensureHub(): Hub {
  const existing = globals[HUB] as Hub | undefined
  if (existing) {
    // A later global dispatcher replacement could otherwise silently bypass us.
    if (globals[MODERN] !== existing.modern || globals[LEGACY] !== existing.legacy) {
      throw new ModelProxyTransportError('model-proxy-dispatcher-replaced')
    }
    return existing
  }
  // Undici 8 initializes BOTH v2 and legacy v1 globals on first import. Capture
  // the Node-fetch dispatcher before requiring it, not after a static import.
  const savedLegacy = globals[LEGACY] as Dispatcher | undefined
  const savedModern = globals[MODERN] as Dispatcher | undefined
  const undici = require('undici') as Undici
  const previousModern = savedModern ?? undici.getGlobalDispatcher()
  const previousLegacy = savedLegacy ?? globals[LEGACY] as Dispatcher
  const instances = new Set<WeakRef<ModelProxyRuntime>>()
  const current = (): Scope | undefined => {
    let selected: Scope | undefined
    for (const reference of instances) {
      const runtime = reference.deref()
      if (!runtime) { instances.delete(reference); continue }
      const scope = runtime.currentScope()
      if (scope && (!selected || scope.order > selected.order)) selected = scope
    }
    return selected
  }
  const modern = transparentDispatcher(previousModern, (options, handler) => {
    const scope = current()
    return scope ? scope.dispatch(options, handler) : previousModern.dispatch(options, handler)
  })
  let undoNative: (() => void) | undefined
  try {
    undici.setGlobalDispatcher(modern)
    // This bridge only adapts scoped legacy handlers to Undici 8. Unscoped
    // Node fetch calls go directly to their own original legacy dispatcher.
    const bridge = globals[LEGACY] as Dispatcher
    const legacy = transparentDispatcher(previousLegacy, (options, handler) => {
      const scope = current()
      return scope ? bridge.dispatch(options, handler) : previousLegacy.dispatch(options, handler)
    })
    globals[LEGACY] = legacy
    if (globals[MODERN] !== modern || globals[LEGACY] !== legacy) throw new Error()
    undoNative = installModelProxyNodeGuards(current)
    const hub: Hub = { undici, instances, modern, legacy, order: 0 }
    Object.defineProperty(globals, HUB, { value: hub, configurable: false })
    return hub
  } catch {
    undoNative?.()
    globals[MODERN] = previousModern
    globals[LEGACY] = previousLegacy
    throw new ModelProxyTransportError('model-proxy-runtime-install-failed')
  }
}

function completeHandler(handler: Parameters<Dispatch>[1], release: () => void): Parameters<Dispatch>[1] {
  return new Proxy(handler, {
    get(target, property) {
      const value = Reflect.get(target, property, target)
      if (property === 'onResponseEnd' || property === 'onResponseError') {
        return (...args: unknown[]) => {
          try { return typeof value === 'function' ? Reflect.apply(value, target, args) : undefined }
          finally { release() }
        }
      }
      return typeof value === 'function' ? value.bind(target) : value
    },
  })
}

export class ModelProxyRuntime {
  readonly #hub: Hub
  readonly #options: ModelProxyRuntimeOptions
  readonly #storage = new AsyncLocalStorage<Scope>()
  readonly #snapshots = new WeakMap<ModelProxySnapshot, ModelProxySnapshot>()
  readonly #generations = new WeakMap<ModelProxySnapshot, Generation>()
  readonly #open = new Set<Generation>()
  #latest?: Generation
  #disposed = false
  #disposePromise?: Promise<void>
  #resolveDispose?: () => void
  #rejectDispose?: (error: Error) => void
  #closeFailed = false

  constructor(options: ModelProxyRuntimeOptions = {}) {
    this.#options = options
    this.#hub = ensureHub()
    this.#hub.instances.add(new WeakRef(this))
  }

  /** A stable owned copy: even accidentally mutable caller input is isolated. */
  snapshot(scope: ModelProxySnapshot): ModelProxySnapshot {
    let copy = this.#snapshots.get(scope)
    if (!copy) {
      copy = createModelProxySnapshot(scope.config, scope.system)
      this.#snapshots.set(scope, copy)
      this.#snapshots.set(copy, copy)
    }
    return copy
  }

  /** Internal hub interface; storage is instance-owned, never a shared ALS. */
  currentScope(): Scope | undefined { return this.#storage.getStore() }

  #assertInstalled(): void {
    if (globals[MODERN] !== this.#hub.modern || globals[LEGACY] !== this.#hub.legacy) {
      throw new ModelProxyTransportError('model-proxy-dispatcher-replaced')
    }
  }

  #generation(input: ModelProxySnapshot): Generation {
    if (this.#disposed) throw new ModelProxyTransportError('model-proxy-runtime-disposed')
    this.#assertInstalled()
    const snapshot = this.snapshot(input)
    let generation = this.#generations.get(snapshot)
    if (!generation || generation.closing) {
      generation = {
        snapshot,
        direct: new this.#hub.undici.Agent(this.#options.agentOptions),
        proxies: new Map(),
        native: new ModelProxyNodeAgents(target => resolveModelProxyRoute(snapshot, target)),
        references: 0, retired: false, closing: false,
      }
      this.#generations.set(snapshot, generation)
      this.#open.add(generation)
    }
    if (this.#latest !== generation) {
      if (this.#latest) { this.#latest.retired = true; this.#tryClose(this.#latest) }
      this.#latest = generation
    }
    generation.retired = false
    return generation
  }

  #retain(generation: Generation): () => void {
    if (generation.closing) throw new ModelProxyTransportError('model-proxy-scope-closed')
    generation.references++
    let released = false
    return () => {
      if (released) return
      released = true
      generation.references--
      this.#tryClose(generation)
    }
  }

  #acquire(input: ModelProxySnapshot): { scope: Scope; release: () => void } {
    const generation = this.#generation(input)
    const finish = this.#retain(generation)
    const scope: Scope = {
      owner: this,
      generation,
      native: generation.native,
      order: ++this.#hub.order,
      released: false,
      assertActive: () => {
        if (scope.released || generation.closing) throw new ModelProxyTransportError('model-proxy-scope-closed')
        this.#assertInstalled()
      },
      retain: () => { scope.assertActive(); return this.#retain(generation) },
      dispatch: (options, handler) => {
        scope.assertActive()
        if (options.upgrade || String(options.method).toUpperCase() === 'CONNECT') {
          throw new ModelProxyTransportError('model-proxy-upgrade-unsupported')
        }
        const origin = String(options.origin)
        if (!/^https?:\/\//.test(origin)) throw new ModelProxyTransportError('model-proxy-target-unsupported')
        const route = resolveModelProxyRoute(generation.snapshot, origin)
        let agent = generation.direct
        if (route.kind === 'proxy') {
          let proxy = generation.proxies.get(route.proxyUrl)
          if (!proxy) {
            proxy = new this.#hub.undici.ProxyAgent({ ...this.#options.proxyAgentOptions, uri: route.proxyUrl })
            generation.proxies.set(route.proxyUrl, proxy)
          }
          agent = proxy
        }
        const release = scope.retain()
        try { return agent.dispatch(options, completeHandler(handler, release)) }
        catch (error) { release(); throw error }
      },
    }
    return { scope, release: () => { scope.released = true; finish() } }
  }

  #tryClose(generation: Generation): void {
    if (!generation.retired || generation.references !== 0 || generation.closing) return
    generation.closing = true
    // No in-flight native requests remain (each holds its own reference).
    generation.native.dispose()
    void Promise.allSettled([generation.direct, ...generation.proxies.values()].map(agent => agent.close())).then(results => {
      if (results.some(result => result.status === 'rejected')) this.#closeFailed = true
      generation.proxies.clear()
      this.#open.delete(generation)
      this.#finishDispose()
    })
  }

  #finishDispose(): void {
    if (!this.#disposed || this.#open.size) return
    if (this.#closeFailed) this.#rejectDispose?.(new ModelProxyTransportError('model-proxy-dispose-failed'))
    else this.#resolveDispose?.()
  }

  #enter<T>(scope: Scope, fn: () => T): T {
    // A stream can resume inside another runtime's newer scope. Order must
    // describe this entry, not when the stream first acquired its lease; never
    // mutate an old entry that background async work may still have inherited.
    const entered = Object.create(scope) as Scope
    Object.defineProperty(entered, 'order', { value: ++this.#hub.order })
    return this.#storage.run(entered, fn)
  }

  /** Only fn's execution/async descendants are scoped, never its awaiter. */
  async run<T>(snapshot: ModelProxySnapshot, fn: () => T | PromiseLike<T>): Promise<T> {
    const lease = this.#acquire(snapshot)
    try { return await this.#enter(lease.scope, fn) }
    finally { lease.release() }
  }

  /**
   * Factory and iterator operations execute in the scope. The consumer's
   * continuation (including tools invoked after yield) executes outside it.
   * Serializing operations matches async-generator next/return/throw ordering.
   */
  wrapStream<T>(snapshot: ModelProxySnapshot, factory: () => AsyncIterable<T>): AsyncIterableIterator<T> {
    const frozen = this.snapshot(snapshot)
    let lease: { scope: Scope; release: () => void } | undefined
    let iterator: AsyncIterator<T> | undefined
    let done = false
    let queue: Promise<unknown> = Promise.resolve()
    const finish = () => { if (!done) { done = true; lease?.release() } }
    const operation = (method: 'next' | 'return' | 'throw', value?: unknown): Promise<IteratorResult<T>> => {
      const result = queue.then(async (): Promise<IteratorResult<T>> => {
        if (done) {
          if (method === 'throw') throw value
          return { done: true, value: method === 'return' ? value as T : undefined as T }
        }
        if (!lease && method !== 'next') {
          finish()
          if (method === 'throw') throw value
          return { done: true, value: value as T }
        }
        try {
          lease ??= this.#acquire(frozen)
          const result = await this.#enter(lease.scope, async () => {
            iterator ??= factory()[Symbol.asyncIterator]()
            if (method === 'throw' && !iterator.throw) {
              await iterator.return?.()
              throw value
            }
            if (method === 'return' && !iterator.return) return { done: true, value: value as T }
            return await iterator[method]!(value as never)
          })
          if (result.done) finish()
          return result
        } catch (error) { finish(); throw error }
      })
      queue = result.catch(() => {})
      return result
    }
    return {
      next: value => operation('next', value),
      return: value => operation('return', value),
      throw: error => operation('throw', error),
      [Symbol.asyncIterator]() { return this },
    }
  }

  /** Stop new scopes; wait for active streams and response bodies, then close. */
  dispose(): Promise<void> {
    if (this.#disposePromise) return this.#disposePromise
    this.#disposed = true
    this.#disposePromise = new Promise((resolve, reject) => { this.#resolveDispose = resolve; this.#rejectDispose = reject })
    for (const generation of this.#open) { generation.retired = true; this.#tryClose(generation) }
    this.#finishDispose()
    // Do not disable ALS: detached async descendants must fail closed instead
    // of falling through to the unrelated pre-installation dispatcher.
    return this.#disposePromise
  }
}

export function createModelProxyRuntime(options?: ModelProxyRuntimeOptions): ModelProxyRuntime {
  return new ModelProxyRuntime(options)
}
