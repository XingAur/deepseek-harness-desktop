/** Model-only routing owner; configuration commits never interrupt existing streams. */

import {
  modelProxyConfigView, parseModelProxyConfig, readModelProxyConfig, writeModelProxyConfig,
  type ModelProxyConfig,
} from './model-proxy.ts'
import { createModelProxySnapshot, resolveModelProxyRoute, type ModelProxySnapshot } from './model-proxy-routing.ts'
import { readSystemProxy, type SystemProxySnapshot } from './system-proxy.ts'
import { ModelProxyRuntime } from './model-proxy-runtime.ts'

const PROBE_URL = 'https://opencode.ai/'
const PROBE_TIMEOUT_MS = 8_000
const SAFE_CODES = new Set([
  'invalid-config', 'invalid-mode', 'invalid-url', 'url-too-long', 'unsupported-proxy-scheme',
  'invalid-bypass', 'manual-url-required', 'config-read-failed', 'config-write-failed',
  'system-proxy-unsupported-platform', 'system-proxy-read-failed', 'system-proxy-invalid-config',
  'system-proxy-pac-unsupported', 'system-proxy-autodiscovery-unsupported',
  'system-proxy-socks-unsupported', 'system-proxy-scoped-unsupported',
  'system-proxy-list-unsupported', 'system-proxy-bypass-unsupported',
  'model-proxy-disposed', 'model-proxy-transport-unsupported', 'model-proxy-runtime-unavailable',
  'invalid-target-url',
])
const CODE_ALIASES: Readonly<Record<string, string>> = {
  'model-proxy-custom-agent-unsupported': 'model-proxy-transport-unsupported',
  'model-proxy-custom-connection-unsupported': 'model-proxy-transport-unsupported',
  'model-proxy-http2-unsupported': 'model-proxy-transport-unsupported',
  'model-proxy-target-unsupported': 'model-proxy-transport-unsupported',
  'model-proxy-runtime-disposed': 'model-proxy-runtime-unavailable',
  'model-proxy-dispatcher-replaced': 'model-proxy-runtime-unavailable',
  'model-proxy-runtime-install-failed': 'model-proxy-runtime-unavailable',
  'model-proxy-native-install-failed': 'model-proxy-runtime-unavailable',
  'model-proxy-scope-closed': 'model-proxy-runtime-unavailable',
}

/** Return a stable diagnostic, never a URL, subprocess output, credential, or raw exception. */
export function modelProxyErrorCode(cause: unknown): string {
  let error = cause
  const seen = new Set<object>()
  while (error !== null && typeof error === 'object' && !seen.has(error)) {
    seen.add(error)
    const record = error as { code?: unknown; message?: unknown; name?: unknown; cause?: unknown; statusCode?: unknown }
    for (const value of [record.code, record.message]) {
      if (typeof value === 'string' && SAFE_CODES.has(value)) return value
      if (typeof value === 'string' && Object.hasOwn(CODE_ALIASES, value)) return CODE_ALIASES[value]!
    }
    const code = typeof record.code === 'string' ? record.code : ''
    const message = typeof record.message === 'string' ? record.message : ''
    if (record.statusCode === 407 || /\b407\b|proxy[^\n]*auth/i.test(message)) return 'proxy-auth'
    if (code === 'ECONNREFUSED') return 'proxy-refused'
    if (code === 'ENOTFOUND' || code === 'EAI_AGAIN') return 'dns'
    if (record.name === 'TimeoutError' || /TIMEOUT/.test(code) || /timed?\s*out|timeout/i.test(message)) return 'timeout'
    if (/CERT|TLS|SSL/.test(code) || /certificate|\bTLS\b|\bSSL\b/i.test(message)) return 'tls'
    error = record.cause
  }
  return 'unknown'
}

export interface ModelNetworkRuntime {
  run<T>(snapshot: ModelProxySnapshot, operation: () => Promise<T>): Promise<T>
  wrapStream<T>(snapshot: ModelProxySnapshot, factory: () => AsyncIterable<T>): AsyncIterable<T>
  dispose(): Promise<void>
}

interface ManagerOptions {
  readonly system?: () => Promise<SystemProxySnapshot>
  readonly runtime?: ModelNetworkRuntime
  readonly probeUrl?: string
  readonly probeTimeoutMs?: number
}

export interface ModelProxyTestResult {
  readonly ok: boolean
  readonly error: string | null
  readonly route?: 'proxy' | 'direct'
  /** Origin of the credential-free probe target, not the proxy server. */
  readonly origin?: string
}

/** Own one profile's settings and request-scoped transports without changing system settings. */
export class ModelProxyManager {
  readonly #home: string
  readonly #runtime: ModelNetworkRuntime
  readonly #system: () => Promise<SystemProxySnapshot>
  readonly #probeUrl: string
  readonly #probeTimeoutMs: number
  #pending: Promise<void> = Promise.resolve()
  #cached: { identity: string; snapshot: ModelProxySnapshot } | undefined
  #disposed = false

  constructor(home: string, options: ManagerOptions = {}) {
    this.#home = home
    this.#runtime = options.runtime ?? new ModelProxyRuntime()
    this.#system = options.system ?? (() => readSystemProxy())
    this.#probeUrl = options.probeUrl ?? PROBE_URL
    this.#probeTimeoutMs = options.probeTimeoutMs ?? PROBE_TIMEOUT_MS
  }

  #assertOpen(): void {
    if (this.#disposed) throw new Error('model-proxy-disposed')
  }

  async #read(): Promise<{ config: ModelProxyConfig; system: SystemProxySnapshot }> {
    this.#assertOpen()
    await this.#pending
    const config = await readModelProxyConfig(this.#home)
    // A failed system read remains explicit; it must never be converted to an absent proxy.
    const system = await this.#system()
    this.#assertOpen()
    return { config, system }
  }

  async view() {
    const { config, system } = await this.#read()
    return modelProxyConfigView(config, system)
  }

  /** Refresh OS facts at a new call boundary, reusing the same immutable snapshot when unchanged. */
  async capture(): Promise<ModelProxySnapshot> {
    const { config, system } = await this.#read()
    const identity = JSON.stringify({ config, system })
    if (this.#cached?.identity === identity) return this.#cached.snapshot
    const snapshot = createModelProxySnapshot(config, system)
    this.#cached = { identity, snapshot }
    return snapshot
  }

  /** Serialize persistence. A failed write leaves the previous published configuration intact. */
  save(input: unknown): Promise<ReturnType<typeof modelProxyConfigView>> {
    this.#assertOpen()
    const operation = this.#pending.then(async () => {
      this.#assertOpen()
      const previous = await readModelProxyConfig(this.#home)
      const next = parseModelProxyConfig(input, previous)
      const system = await this.#system()
      createModelProxySnapshot(next, system)
      this.#assertOpen()
      const saved = await writeModelProxyConfig(this.#home, next)
      this.#cached = undefined
      return modelProxyConfigView(saved, system)
    })
    this.#pending = operation.then(() => {}, () => {})
    return operation
  }

  async run<T>(operation: () => Promise<T>): Promise<T> {
    const snapshot = await this.capture()
    return await this.#runtime.run(snapshot, operation)
  }

  /** The consumer receives chunks outside the model network scope. */
  async *stream<T>(factory: () => AsyncIterable<T>): AsyncIterable<T> {
    const snapshot = await this.capture()
    yield* this.#runtime.wrapStream(snapshot, factory)
  }

  /** An explicit network-only probe; it never writes settings or submits a generation request. */
  async test(input: unknown): Promise<ModelProxyTestResult> {
    let route: 'proxy' | 'direct' | undefined
    const origin = new URL(this.#probeUrl).origin
    try {
      const { config, system } = await this.#read()
      const proposed = parseModelProxyConfig(input, config)
      const snapshot = createModelProxySnapshot(proposed, system)
      route = resolveModelProxyRoute(snapshot, this.#probeUrl).kind
      return await this.#runtime.run(snapshot, async () => {
        const response = await fetch(this.#probeUrl, {
          method: 'HEAD', redirect: 'manual', signal: AbortSignal.timeout(this.#probeTimeoutMs),
          headers: { accept: 'text/html, application/json' },
        })
        await response.body?.cancel()
        if (response.status === 407) return { ok: false, error: 'proxy-auth', route, origin }
        if (response.status >= 500) return { ok: false, error: 'request-failed', route, origin }
        return { ok: true, error: null, route, origin }
      })
    } catch (cause) {
      return { ok: false, error: modelProxyErrorCode(cause), ...(route ? { route } : {}), origin }
    }
  }

  async dispose(): Promise<void> {
    if (this.#disposed) return
    this.#disposed = true
    await this.#pending
    await this.#runtime.dispose()
    this.#cached = undefined
  }
}
