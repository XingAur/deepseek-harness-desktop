/** Scoped Node HTTP support. Unscoped calls are delegated with their original arguments. */
import http from 'node:http'
import https from 'node:https'
import http2 from 'node:http2'
import { syncBuiltinESMExports } from 'node:module'
import { urlToHttpOptions } from 'node:url'
import { HttpProxyAgent } from 'http-proxy-agent'
import { HttpsProxyAgent } from 'https-proxy-agent'
import type { ModelProxyRoute } from './model-proxy-routing.ts'

export class ModelProxyTransportError extends Error {
  readonly code: string
  constructor(code: string) { super(code); this.name = 'ModelProxyTransportError'; this.code = code }
}

export interface ModelProxyNodeScope {
  readonly native: ModelProxyNodeAgents
  assertActive(): void
  retain(): () => void
}

type Options = http.RequestOptions & https.RequestOptions
const HttpAgent = http.Agent
const HttpsAgent = https.Agent
const plainHttpConnection = HttpAgent.prototype.createConnection
const plainHttpsConnection = HttpsAgent.prototype.createConnection

/** Agents belong to one immutable snapshot, never the process or another generation. */
export class ModelProxyNodeAgents {
  readonly #resolve: (target: URL) => ModelProxyRoute
  readonly #agents = new Set<http.Agent>()
  readonly #cache = new Map<object, Map<string, http.Agent>>()
  readonly #noAgent = Object.freeze({})
  constructor(resolve: (target: URL) => ModelProxyRoute) { this.#resolve = resolve }

  agent(target: URL, options: Options): http.Agent {
    // A caller-supplied socket or transport can already be connected elsewhere.
    // Silently dropping it would also lose its TLS/mTLS/security semantics.
    if (options.createConnection || options.socketPath || (options as any).socket) {
      throw new ModelProxyTransportError('model-proxy-custom-connection-unsupported')
    }
    const secure = target.protocol === 'https:'
    const source = options.agent === false ? undefined : options.agent ?? (secure ? https.globalAgent : http.globalAgent)
    const prototype = secure ? HttpsAgent.prototype : HttpAgent.prototype
    if (source && (source.constructor !== (secure ? HttpsAgent : HttpAgent)
      || source.createConnection !== (secure ? plainHttpsConnection : plainHttpConnection)
      || ['addRequest', 'getName', 'createSocket', 'removeSocket', 'reuseSocket', 'keepSocketAlive']
        .some(method => (source as any)[method] !== (prototype as any)[method]))) {
      throw new ModelProxyTransportError('model-proxy-custom-agent-unsupported')
    }
    const sourceOptions = (source as (http.Agent & { options?: http.AgentOptions & https.AgentOptions & Record<string, unknown> }) | undefined)?.options ?? {}
    const route = this.#resolve(target)
    const headers = Array.isArray(options.headers)
      ? options.headers.reduce<[string, unknown][]>((entries, value, index, values) => {
        if (index % 2 === 0) entries.push([String(value), values[index + 1]])
        return entries
      }, [])
      : Object.entries(options.headers ?? {})
    if (String(options.method).toUpperCase() === 'CONNECT' || headers.some(([name]) => name.toLowerCase() === 'upgrade')) {
      throw new ModelProxyTransportError('model-proxy-upgrade-unsupported')
    }
    if (route.kind === 'proxy' && !secure) {
      const host = headers.find(([name]) => name.toLowerCase() === 'host')?.[1]
      // http-proxy-agent derives absolute-form authority from Host; a virtual
      // Host different from the selected destination is not safely routable.
      if (host !== undefined) {
        let sameOrigin = false
        try { sameOrigin = new URL(`${target.protocol}//${String(host)}`).origin === target.origin } catch { /* reject below */ }
        if (!sameOrigin) throw new ModelProxyTransportError('model-proxy-native-host-unsupported')
      }
    }
    const cacheKey = source ?? this.#noAgent
    let agents = this.#cache.get(cacheKey)
    if (!agents) { agents = new Map(); this.#cache.set(cacheKey, agents) }
    const key = `${target.protocol}|${route.kind === 'proxy' ? route.proxyUrl : 'direct'}`
    let agent = agents.get(key)
    if (!agent) {
      // Core Agent options carry CA, cert/key, ciphers, SNI, family, lookup,
      // localAddress, etc. Preserve those, but never inherit proxyEnv.
      const base = { ...(sourceOptions), proxyEnv: {}, ...(options.agent === false ? { keepAlive: false } : {}) }
      if ((base as any).createConnection || (base as any).socketPath || (base as any).socket) {
        throw new ModelProxyTransportError('model-proxy-custom-connection-unsupported')
      }
      // Destination TLS identity (especially a client cert/key) must NEVER
      // become the proxy connection's TLS identity. Proxy TLS uses system CAs.
      const proxyOptions = Object.fromEntries(['keepAlive', 'keepAliveMsecs', 'maxSockets', 'maxTotalSockets', 'maxFreeSockets', 'scheduling', 'timeout', 'localAddress', 'family', 'lookup']
        .filter(name => name in base).map(name => [name, (base as any)[name]]))
      agent = route.kind === 'direct'
        ? secure ? new HttpsAgent(base) : new HttpAgent(base)
        : secure ? new HttpsProxyAgent(route.proxyUrl, proxyOptions) : new HttpProxyAgent(route.proxyUrl, proxyOptions)
      agents.set(key, agent)
      this.#agents.add(agent)
    }
    // Proxy-agent invokes TLS with request options, not the original core
    // agent's options. Include the latter so custom trusted roots/mTLS survive.
    for (const [name, value] of Object.entries(sourceOptions)) {
      if (name !== 'proxyEnv' && !(name in options)) (options as any)[name] = value
    }
    return agent
  }

  dispose(): void { for (const agent of this.#agents) agent.destroy(); this.#agents.clear(); this.#cache.clear() }
}

function normalize(args: unknown[], protocol: 'http:' | 'https:'): { options: Options; callback: unknown; target: URL } {
  const first = args[0]
  let options: Options
  let callback: unknown
  if (typeof first === 'string' || first instanceof URL) {
    const url = first instanceof URL ? first : new URL(first)
    options = { ...urlToHttpOptions(url) }
    if (typeof args[1] === 'object' && args[1] !== null) {
      Object.assign(options, args[1])
      callback = args[2]
    } else callback = args[1]
  } else if (first && typeof first === 'object') {
    options = { ...first }
    callback = args[1]
  } else throw new ModelProxyTransportError('model-proxy-native-request-invalid')
  const scheme = options.protocol ?? protocol
  if (scheme !== protocol) throw new ModelProxyTransportError('model-proxy-native-protocol-invalid')
  const hostname = String(options.hostname ?? options.host ?? 'localhost')
  const host = hostname.includes(':') && !hostname.startsWith('[') ? `[${hostname}]` : hostname
  let target: URL
  try { target = new URL(`${scheme}//${host}${options.port ? `:${options.port}` : ''}`) }
  catch { throw new ModelProxyTransportError('model-proxy-native-request-invalid') }
  // Absolute-form requests usually mean an existing custom proxy transport.
  // Routing that proxy as if it were the model target would be a policy bypass.
  if (options.path && !options.path.startsWith('/')) throw new ModelProxyTransportError('model-proxy-native-path-unsupported')
  return { options, callback, target }
}

/**
 * Installed once by the dispatcher hub, never on a settings change. Captured
 * CJS function references, pre-existing HTTP2 sessions and direct new
 * ClientRequest() cannot be intercepted safely; provider adapters must not use
 * them. The tested SDK path is request()/get() (including live ESM bindings).
 */
export function installModelProxyNodeGuards(current: () => ModelProxyNodeScope | undefined): () => void {
  const original = { httpRequest: http.request, httpGet: http.get, httpsRequest: https.request, httpsGet: https.get, http2Connect: http2.connect }
  function request(protocol: 'http:' | 'https:', previous: typeof http.request, receiver: unknown, args: unknown[]) {
    const scope = current()
    if (!scope) return Reflect.apply(previous, receiver, args)
    scope.assertActive()
    const { options, callback, target } = normalize(args, protocol)
    options.agent = scope.native.agent(target, options)
    // An explicit per-request proxyEnv must not override the selected route.
    delete (options as any).proxyEnv
    const release = scope.retain()
    try {
      const result = Reflect.apply(previous, receiver, callback === undefined ? [options] : [options, callback])
      result.once('close', release)
      return result
    } catch (error) { release(); throw error }
  }
  const httpRequest = function (this: unknown, ...args: unknown[]) { return request('http:', original.httpRequest, this, args) } as typeof http.request
  const httpsRequest = function (this: unknown, ...args: unknown[]) { return request('https:', original.httpsRequest, this, args) } as typeof https.request
  const httpGet = function (this: unknown, ...args: unknown[]) {
    if (!current()) return Reflect.apply(original.httpGet, this, args)
    const result = request('http:', original.httpRequest, this, args); result.end(); return result
  } as typeof http.get
  const httpsGet = function (this: unknown, ...args: unknown[]) {
    if (!current()) return Reflect.apply(original.httpsGet, this, args)
    const result = request('https:', original.httpsRequest, this, args); result.end(); return result
  } as typeof https.get
  const http2Connect = function (this: unknown, ...args: unknown[]) {
    if (current()) throw new ModelProxyTransportError('model-proxy-http2-unsupported')
    const session = Reflect.apply(original.http2Connect, this, args)
    const previous = session.request
    // Also guard sessions created outside a model call after installation.
    session.request = function (...requestArgs: Parameters<typeof previous>) {
      if (current()) throw new ModelProxyTransportError('model-proxy-http2-unsupported')
      return Reflect.apply(previous, this, requestArgs)
    }
    return session
  } as typeof http2.connect
  try {
    http.request = httpRequest; http.get = httpGet
    https.request = httpsRequest; https.get = httpsGet; http2.connect = http2Connect
    syncBuiltinESMExports()
  } catch {
    http.request = original.httpRequest; http.get = original.httpGet
    https.request = original.httpsRequest; https.get = original.httpsGet; http2.connect = original.http2Connect
    syncBuiltinESMExports()
    throw new ModelProxyTransportError('model-proxy-native-install-failed')
  }
  // Used only to roll back a failed hub installation. Normal runtime disposal
  // leaves the single transparent guard in place so detached stale scopes fail.
  return () => {
    if (http.request === httpRequest) http.request = original.httpRequest
    if (http.get === httpGet) http.get = original.httpGet
    if (https.request === httpsRequest) https.request = original.httpsRequest
    if (https.get === httpsGet) https.get = original.httpsGet
    if (http2.connect === http2Connect) http2.connect = original.http2Connect
    syncBuiltinESMExports()
  }
}
