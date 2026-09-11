/** Outbound WebSocket tunnel carrying relayed phone requests to the loopback WebServer. */

import { request as requestHttp } from 'node:http'
import { connect as connectTcp, Socket } from 'node:net'
import WebSocket from 'ws'
import { DESKTOP_RENDERER_ACCESS_HEADER } from './desktop-browser-access.ts'

/** States surfaced to the pairing dialog and the Host snapshot. */
export type RemoteRelayTunnelState = 'connecting' | 'ready' | 'failed'

/** Launcher-resolved values fixed for one tunnel lifetime. */
export interface RemoteRelayTunnelOptions {
  /** Relay WebSocket endpoint, for example `wss://relay.example.com/host`. */
  readonly relayUrl: string
  /** Pairing id segment the relay routes `/r/<pairId>/…` by. */
  readonly pairId: string
  /** Pairing secret presented in the tunnel's first frame. */
  readonly secret: string
  /** Loopback WebServer port tunneled requests are forwarded to. */
  readonly targetPort: number
  /** Structured log sink; never throws. */
  readonly log?: (message: string) => void
}

export interface RemoteRelayTunnelSnapshot {
  readonly state: RemoteRelayTunnelState
  readonly error: string | null
}

interface ActiveRequest {
  readonly outgoing: import('node:http').ClientRequest
}

interface ActiveUpgrade {
  readonly upstream: Socket
}

const CONNECT_TIMEOUT_MS = 15_000
const REQUEST_TIMEOUT_MS = 30_000
const RECONNECT_BASE_MS = 1_000
const RECONNECT_MAX_MS = 30_000
const MAX_FRAME_BYTES = 16 * 1024 * 1024

const HOP_BY_HOP_HEADERS = new Set([
  'connection',
  'keep-alive',
  'proxy-authenticate',
  'proxy-authorization',
  'proxy-connection',
  'te',
  'trailers',
  'transfer-encoding',
])

function isForwardingIdentityHeader(name: string): boolean {
  const lower = name.toLowerCase()
  return lower === DESKTOP_RENDERER_ACCESS_HEADER.toLowerCase()
    || lower === 'forwarded'
    || lower.startsWith('x-forwarded-')
}

function headerValue(value: unknown): string | null {
  if (typeof value === 'string') return value
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  if (Array.isArray(value)) {
    const parts = value.filter(item => typeof item === 'string') as string[]
    return parts.length === 0 ? null : parts.join(', ')
  }
  return null
}

/** Drop the headers a remote client must never speak for the installation. */
export function sanitizedTunnelHeaders(
  headers: ReadonlyArray<readonly [string, unknown]>,
  options?: { readonly hopByHop?: boolean },
): Array<[string, string]> {
  const stripHopByHop = options?.hopByHop === true
  const result: Array<[string, string]> = []
  for (const [name, raw] of headers) {
    const lower = name.toLowerCase()
    if (isForwardingIdentityHeader(name)) continue
    if (stripHopByHop && HOP_BY_HOP_HEADERS.has(lower)) continue
    const value = headerValue(raw)
    if (value === null) continue
    result.push([name, value])
  }
  return result
}

/** Rebuild one raw HTTP/1.1 request head, the same wire form an upgrade needs. */
export function rawTunnelRequest(
  method: string,
  path: string,
  headers: ReadonlyArray<readonly [string, string]>,
): string {
  const lines = [`${method} ${path} HTTP/1.1`]
  for (const [name, value] of headers) lines.push(`${name}: ${value}`)
  lines.push('', '')
  return lines.join('\r\n')
}

/**
 * One outbound relay connection. Requests arriving as tunnel frames are
 * answered by the loopback WebServer; upgrades are bridged as raw sockets,
 * mirroring the LAN HTTPS edge's forwarding semantics with the TLS listener
 * replaced by the relay's multiplexed channel.
 */
export class RemoteRelayTunnel {
  private readonly options: RemoteRelayTunnelOptions
  private readonly requests = new Map<string, ActiveRequest>()
  private readonly upgrades = new Map<string, ActiveUpgrade>()
  private socket: WebSocket | null = null
  private state: RemoteRelayTunnelState = 'connecting'
  private stateError: string | null = null
  private disposed = false
  private reconnectTimer: NodeJS.Timeout | undefined
  private reconnectAttempts = 0

  constructor(options: RemoteRelayTunnelOptions) {
    if (!/^wss?:\/\//u.test(options.relayUrl) || options.relayUrl.includes('\0')) {
      throw new Error(`dsh-plugin-desktop: remote relay URL must be a ws:// or wss:// endpoint: ${JSON.stringify(options.relayUrl)}`)
    }
    if (options.pairId.length === 0 || options.secret.length === 0 || options.targetPort <= 0) {
      throw new Error('dsh-plugin-desktop: remote relay pairing requires an id, a secret, and a target port')
    }
    this.options = options
  }

  snapshot(): RemoteRelayTunnelSnapshot {
    return { state: this.state, error: this.stateError }
  }

  start(): void {
    if (this.disposed) throw new Error('dsh-plugin-desktop: remote relay tunnel already disposed')
    this.connect()
  }

  dispose(): void {
    this.disposed = true
    if (this.reconnectTimer !== undefined) clearTimeout(this.reconnectTimer)
    for (const [, entry] of this.requests) entry.outgoing.destroy()
    this.requests.clear()
    for (const [, entry] of this.upgrades) entry.upstream.destroy()
    this.upgrades.clear()
    this.socket?.close(1000, 'desktop stopped the tunnel')
    this.socket = null
  }

  private connect(): void {
    this.state = 'connecting'
    this.stateError = null
    let socket: WebSocket
    try {
      socket = new WebSocket(this.options.relayUrl, {
        handshakeTimeout: CONNECT_TIMEOUT_MS,
        perMessageDeflate: false,
        maxPayload: MAX_FRAME_BYTES,
      })
    } catch (cause) {
      this.scheduleReconnect(cause instanceof Error ? cause.message : String(cause))
      return
    }
    this.socket = socket
    socket.on('open', () => {
      this.reconnectAttempts = 0
      socket.send(JSON.stringify({ t: 'hello', v: 1, pair: this.options.pairId, secret: this.options.secret }))
    })
    socket.on('message', data => {
      let frame: Record<string, unknown>
      try {
        frame = JSON.parse(String(data)) as Record<string, unknown>
      } catch {
        return
      }
      this.handleFrame(frame).catch(cause => {
        this.options.log?.(`relay frame failed: ${cause instanceof Error ? cause.message : String(cause)}`)
      })
    })
    socket.on('close', () => {
      if (this.socket === socket) this.socket = null
      this.failPending('desktop tunnel disconnected')
      if (!this.disposed) this.scheduleReconnect('relay connection closed')
    })
    socket.on('error', () => {
      // 'close' always follows; logging happens there.
    })
  }

  private scheduleReconnect(reason: string): void {
    if (this.disposed || this.reconnectTimer !== undefined) return
    this.state = 'failed'
    this.stateError = reason
    const delay = Math.min(RECONNECT_BASE_MS * 2 ** this.reconnectAttempts, RECONNECT_MAX_MS)
    this.reconnectAttempts += 1
    this.options.log?.(`relay disconnected (${reason}); reconnecting in ${String(delay)}ms`)
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = undefined
      if (!this.disposed) this.connect()
    }, delay)
  }

  private send(frame: Record<string, unknown>): void {
    if (this.socket?.readyState !== WebSocket.OPEN) return
    try {
      this.socket.send(JSON.stringify(frame))
    } catch (cause) {
      this.options.log?.(`relay send failed: ${cause instanceof Error ? cause.message : String(cause)}`)
    }
  }

  private failPending(reason: string): void {
    for (const [id, entry] of this.requests) {
      entry.outgoing.destroy()
      this.requests.delete(id)
      this.send({ t: 'fail', id, status: 502, reason })
    }
    for (const [id, entry] of this.upgrades) {
      entry.upstream.destroy()
      this.upgrades.delete(id)
      this.send({ t: 'upfail', id, status: 502 })
    }
  }

  private async handleFrame(frame: Record<string, unknown>): Promise<void> {
    if (frame.t === 'ready') {
      this.state = 'ready'
      this.stateError = null
      return
    }
    const id = typeof frame.id === 'string' ? frame.id : ''
    if (id === '') return
    if (frame.t === 'req') {
      this.forwardRequest(id, frame)
      return
    }
    if (frame.t === 'body') {
      this.requests.get(id)?.outgoing.write(Buffer.from(String(frame.d ?? ''), 'base64'))
      return
    }
    if (frame.t === 'end') {
      this.requests.get(id)?.outgoing.end()
      return
    }
    if (frame.t === 'abort') {
      const entry = this.requests.get(id)
      if (entry !== undefined) {
        this.requests.delete(id)
        entry.outgoing.destroy()
      }
      return
    }
    if (frame.t === 'upg') {
      this.forwardUpgrade(id, frame)
      return
    }
    if (frame.t === 'ud') {
      this.upgrades.get(id)?.upstream.write(Buffer.from(String(frame.d ?? ''), 'base64'))
      return
    }
    if (frame.t === 'uc') {
      const entry = this.upgrades.get(id)
      if (entry !== undefined) {
        this.upgrades.delete(id)
        entry.upstream.destroy()
      }
    }
  }

  private frameHeaders(frame: Record<string, unknown>, hopByHop = false): Array<[string, string]> {
    const raw = Array.isArray(frame.headers) ? frame.headers : []
    const headers: Array<[string, unknown]> = []
    for (const entry of raw) {
      if (!Array.isArray(entry) || typeof entry[0] !== 'string') continue
      headers.push([entry[0], entry[1]])
    }
    return sanitizedTunnelHeaders(headers, { hopByHop })
  }

  private forwardRequest(id: string, frame: Record<string, unknown>): void {
    const method = typeof frame.method === 'string' && frame.method !== '' ? frame.method : 'GET'
    const path = typeof frame.path === 'string' && frame.path !== '' ? frame.path : '/'
    let outgoing: import('node:http').ClientRequest
    try {
      outgoing = requestHttp({
        host: '127.0.0.1',
        port: this.options.targetPort,
        method,
        path,
        headers: Object.fromEntries(this.frameHeaders(frame, true)),
        timeout: REQUEST_TIMEOUT_MS,
      }, response => {
      const headers: Array<[string, string]> = []
      for (const [name, value] of Object.entries(response.headers)) {
        if (value === undefined) continue
        for (const item of Array.isArray(value) ? value : [value]) {
          headers.push([name, String(item)])
        }
      }
      this.send({ t: 'res', id, status: response.statusCode ?? 502, headers })
      response.on('data', chunk => this.send({ t: 'body', id, d: chunk.toString('base64') }))
      response.on('end', () => {
        this.requests.delete(id)
        this.send({ t: 'fin', id })
      })
      response.on('error', () => {
        this.requests.delete(id)
        this.send({ t: 'fail', id, status: 502 })
      })
    })
    } catch (cause) {
      this.options.log?.(`relay forward failed: ${cause instanceof Error ? cause.message : String(cause)}`)
      this.send({ t: 'fail', id, status: 502 })
      return
    }
    outgoing.on('timeout', () => outgoing.destroy())
    outgoing.on('error', () => {
      if (this.requests.delete(id)) this.send({ t: 'fail', id, status: 502 })
    })
    this.requests.set(id, { outgoing })
  }

  private forwardUpgrade(id: string, frame: Record<string, unknown>): void {
    const method = typeof frame.method === 'string' && frame.method !== '' ? frame.method : 'GET'
    const path = typeof frame.path === 'string' && frame.path !== '' ? frame.path : '/'
    const headers = this.frameHeaders(frame)
    const upstream = connectTcp(this.options.targetPort, '127.0.0.1')
    upstream.setTimeout(CONNECT_TIMEOUT_MS, () => upstream.destroy())
    let head = Buffer.alloc(0)
    let answered = false
    const onUpstreamData = (chunk: Buffer) => {
      if (!answered) {
        head = Buffer.concat([head, chunk])
        const marker = head.indexOf('\r\n\r\n')
        if (marker === -1) return
        answered = true
        const headText = head.subarray(0, marker).toString('latin1')
        const [statusLine = '', ...headerLines] = headText.split('\r\n')
        const status = Number.parseInt(statusLine.split(' ')[1] ?? '', 10)
        const responseHeaders: Array<[string, string]> = []
        for (const line of headerLines) {
          const colon = line.indexOf(':')
          if (colon === -1) continue
          responseHeaders.push([line.slice(0, colon).trim(), line.slice(colon + 1).trim()])
        }
        upstream.off('data', onUpstreamData)
        if (status === 101) {
          this.upgrades.set(id, { upstream })
          upstream.setTimeout(0)
          upstream.on('data', tail => this.send({ t: 'ud', id, d: tail.toString('base64') }))
          this.send({ t: 'upok', id, headers: responseHeaders })
          const rest = head.subarray(marker + 4)
          if (rest.length > 0) this.send({ t: 'ud', id, d: rest.toString('base64') })
        } else {
          this.send({ t: 'upfail', id, status: Number.isNaN(status) ? 502 : status })
          upstream.destroy()
        }
        return
      }
      this.send({ t: 'ud', id, d: chunk.toString('base64') })
    }
    upstream.on('data', onUpstreamData)
    upstream.on('error', () => {
      if (!answered) {
        answered = true
        this.send({ t: 'upfail', id, status: 502 })
      }
      this.upgrades.delete(id)
    })
    upstream.on('connect', () => {
      upstream.write(rawTunnelRequest(method, path, headers))
    })
  }
}
