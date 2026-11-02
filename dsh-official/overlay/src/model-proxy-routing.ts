/** Pure, request-scoped model routing. Never mutates process/global transport state. */

import { isIP } from 'node:net'
import { domainToASCII } from 'node:url'
import type { ModelProxyConfig } from './model-proxy.ts'
import type { SystemProxySnapshot } from './system-proxy.ts'

export interface ModelProxySnapshot {
  readonly config: ModelProxyConfig
  readonly system: SystemProxySnapshot
}

export type ModelProxyRoute =
  | { readonly kind: 'direct'; readonly reason: string }
  | { readonly kind: 'proxy'; readonly proxyUrl: string; readonly reason: string }

/** Only a stable, credential-free code is exposed to callers. */
export class SystemProxyError extends Error {
  readonly code: string
  constructor(code: string) {
    super(code)
    this.name = 'SystemProxyError'
    this.code = code
  }
}

type BypassSource = 'user' | 'system'
const MAX_BYPASS_RULES = 256
const MAX_BYPASS_LENGTH = 256
const TRANSPORT_PROTOCOLS = new Set(['http:', 'https:', 'ws:', 'wss:'])

function invalidBypass(): never { throw new Error('invalid-bypass') }

function ipv4Number(host: string): bigint | undefined {
  if (isIP(host) !== 4) return undefined
  return host.split('.').reduce((value, octet) => (value << 8n) | BigInt(octet), 0n)
}

function ipv6Number(host: string): bigint | undefined {
  let text = host.toLowerCase().replace(/^\[|\]$/g, '')
  if (isIP(text) !== 6 || text.includes('%')) return undefined
  if (text.includes('.')) {
    const index = text.lastIndexOf(':')
    const ipv4 = ipv4Number(text.slice(index + 1))
    if (ipv4 === undefined) return undefined
    text = `${text.slice(0, index)}:${(ipv4 >> 16n).toString(16)}:${(ipv4 & 0xffffn).toString(16)}`
  }
  const [left, right] = text.split('::')
  const start = left ? left.split(':') : []
  const end = right ? right.split(':') : []
  const parts = text.includes('::')
    ? [...start, ...Array(8 - start.length - end.length).fill('0'), ...end]
    : start
  if (parts.length !== 8) return undefined
  return parts.reduce((value, part) => (value << 16n) | BigInt(`0x${part}`), 0n)
}

function canonicalHost(host: string): string {
  const unwrapped = host.replace(/^\[|\]$/g, '').replace(/\.$/, '').toLowerCase()
  if (isIP(unwrapped) === 6) return new URL(`http://[${unwrapped}]`).hostname.slice(1, -1)
  if (isIP(unwrapped) === 4) return unwrapped
  return domainToASCII(unwrapped)
}

function parseRule(rule: string): { host: string; port?: string } {
  if (rule.startsWith('[')) {
    const match = /^\[([^\]]+)\](?::([0-9]+))?$/.exec(rule)
    if (!match || isIP(match[1]!) !== 6) return invalidBypass()
    return { host: match[1]!, port: match[2] }
  }
  if (isIP(rule) === 6) return { host: rule }
  const pieces = rule.split(':')
  if (pieces.length > 2 || (pieces.length === 2 && !/^[0-9]+$/.test(pieces[1]!))) return invalidBypass()
  return { host: pieces[0]!, port: pieces[1] }
}

function normalizeCidr(rule: string): string {
  const parts = rule.split('/')
  if (parts.length !== 2 || !/^\d+$/.test(parts[1]!)) return invalidBypass()
  let host = parts[0]!.replace(/^\[|\]$/g, '')
  // macOS commonly emits shortened IPv4 networks, e.g. 169.254/16.
  if (/^\d+(?:\.\d+){0,3}$/.test(host)) {
    const octets = host.split('.').map(Number)
    if (octets.some(value => value > 255)) return invalidBypass()
    host = [...octets, ...Array(4 - octets.length).fill(0)].join('.')
  }
  const family = isIP(host)
  const prefix = Number(parts[1])
  if (!family || host.includes('%') || prefix > (family === 4 ? 32 : 128)) return invalidBypass()
  return `${canonicalHost(host)}/${prefix}`
}

/** Validate before saving, rather than silently ignoring a malformed bypass rule. */
export function parseProxyBypass(value: unknown, source: BypassSource = 'user'): readonly string[] {
  if (!Array.isArray(value) || value.length > MAX_BYPASS_RULES) return invalidBypass()
  const result: string[] = []
  for (const item of value) {
    if (typeof item !== 'string') return invalidBypass()
    const rule = item.trim().toLowerCase()
    if (!rule || rule.length > MAX_BYPASS_LENGTH || /[\s@\\?#]/.test(rule)) return invalidBypass()
    if (rule === '<local>' && source === 'system') {
      result.push(rule)
      continue
    }
    if (rule.includes('/')) {
      result.push(normalizeCidr(rule))
      continue
    }
    const { host, port } = parseRule(rule)
    if (port !== undefined && (Number(port) < 1 || Number(port) > 65535)) return invalidBypass()
    let normalized = canonicalHost(host)
    if (!normalized) return invalidBypass()
    if (!isIP(normalized)) {
      // The system may specify arbitrary * globs. Users may use a whole-domain
      // wildcard or a leading *. suffix; a plain domain includes subdomains.
      const domain = (host.startsWith('.') ? host.slice(1) : host).replace(/\.$/, '')
      if (!domain || (source === 'user' && domain !== '*' && domain.replace(/^\*\./, '').includes('*'))) return invalidBypass()
      const labels = domain.split('.')
      if (labels.some(label => !label || !/^[\p{L}\p{N}_*-]+$/u.test(label))) return invalidBypass()
      // Add a sentinel suffix while converting a label: domainToASCII('123')
      // alone is normalized as an IPv4 address by WHATWG URL semantics.
      normalized = labels.map(label => label.includes('*') ? label : domainToASCII(`${label}.invalid`).slice(0, -8)).join('.')
      if (!normalized || normalized.length > 253 || normalized.split('.').some(label => !label || label.length > 63 || label.startsWith('-') || label.endsWith('-'))) return invalidBypass()
      if (host.startsWith('.')) normalized = `.${normalized}`
    } else if (isIP(normalized) === 6 && port !== undefined) {
      normalized = `[${normalized}]`
    }
    result.push(`${normalized}${port === undefined ? '' : `:${Number(port)}`}`)
  }
  return Object.freeze([...new Set(result)])
}

function targetUrl(target: string | URL): URL {
  try {
    const url = new URL(String(target))
    if (!TRANSPORT_PROTOCOLS.has(url.protocol)) throw new Error()
    return url
  } catch {
    throw new SystemProxyError('invalid-target-url')
  }
}

export function isLoopbackHost(hostname: string): boolean {
  const host = canonicalHost(hostname)
  if (host === 'localhost' || host.endsWith('.localhost')) return true
  const ipv4 = ipv4Number(host)
  if (ipv4 !== undefined) return ipv4 >> 24n === 127n
  const ipv6 = ipv6Number(host)
  return ipv6 === 1n || (ipv6 !== undefined && ipv6 >> 32n === 0xffffn && (ipv6 & 0xffffffffn) >> 24n === 127n)
}

function cidrMatches(host: string, rule: string): boolean {
  const [network, size] = rule.split('/')
  const family = isIP(network!)
  if (!family || isIP(host) !== family) return false
  const address = family === 4 ? ipv4Number(host) : ipv6Number(host)
  const base = family === 4 ? ipv4Number(network!) : ipv6Number(network!)
  const shift = BigInt((family === 4 ? 32 : 128) - Number(size))
  return address !== undefined && base !== undefined && address >> shift === base >> shift
}

function matchesRules(url: URL, rules: readonly string[], source: BypassSource): boolean {
  const host = canonicalHost(url.hostname)
  const port = url.port || (url.protocol === 'https:' || url.protocol === 'wss:' ? '443' : '80')
  for (const rule of rules) {
    if (rule === '<local>') {
      if (!isIP(host) && !host.includes('.')) return true
      continue
    }
    if (rule.includes('/')) {
      if (cidrMatches(host, rule)) return true
      continue
    }
    const entry = parseRule(rule)
    if (entry.port !== undefined && entry.port !== port) continue
    const pattern = canonicalHost(entry.host)
    if (isIP(pattern)) {
      if (host === pattern) return true
    } else if (pattern.includes('*')) {
      const escaped = pattern.split('*').map(part => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('.*')
      if (new RegExp(`^${escaped}$`, 'i').test(host)) return true
    } else {
      const suffix = pattern.startsWith('.') ? pattern.slice(1) : pattern
      if (host === suffix || ((source === 'user' || pattern.startsWith('.')) && host.endsWith(`.${suffix}`))) return true
    }
  }
  return false
}

export function matchesProxyBypass(target: string | URL, rules: readonly string[], source: BypassSource = 'user'): boolean {
  return matchesRules(targetUrl(target), parseProxyBypass(rules, source), source)
}

/** Copies every mutable input; later edits cannot reroute an in-flight request. */
export function createModelProxySnapshot(config: ModelProxyConfig, system: SystemProxySnapshot): ModelProxySnapshot {
  return Object.freeze({
    config: Object.freeze({ mode: config.mode, url: config.url, bypass: parseProxyBypass(config.bypass) }),
    system: Object.freeze({
      status: system.status, httpProxy: system.httpProxy, httpsProxy: system.httpsProxy,
      bypass: parseProxyBypass(system.bypass, 'system'),
      ...(system.error === undefined ? {} : { error: system.error }),
    }),
  })
}

/** A selected proxy is a requirement, not a hint: transport errors must propagate. */
export function resolveModelProxyRoute(snapshot: ModelProxySnapshot, target: string | URL): ModelProxyRoute {
  const url = targetUrl(target)
  const { config, system } = snapshot
  if (isLoopbackHost(url.hostname)) return { kind: 'direct', reason: 'loopback' }
  if (config.mode === 'direct') return { kind: 'direct', reason: 'explicit-direct' }
  if (matchesRules(url, config.bypass, 'user')) return { kind: 'direct', reason: 'user-bypass' }
  if ((system.status === 'ready' || system.status === 'direct') && matchesRules(url, system.bypass, 'system')) {
    return { kind: 'direct', reason: 'system-bypass' }
  }
  if (config.mode === 'manual') {
    if (!config.url) throw new SystemProxyError('manual-url-required')
    return { kind: 'proxy', proxyUrl: config.url, reason: 'manual' }
  }
  if (config.mode !== 'system') throw new SystemProxyError('invalid-mode')
  if (system.status === 'error' || system.status === 'unsupported') {
    throw new SystemProxyError(system.error || 'system-proxy-read-failed')
  }
  if (system.status !== 'ready' && system.status !== 'direct') throw new SystemProxyError('system-proxy-invalid-config')
  const proxyUrl = url.protocol === 'https:' || url.protocol === 'wss:' ? system.httpsProxy : system.httpProxy
  return proxyUrl ? { kind: 'proxy', proxyUrl, reason: 'system' } : { kind: 'direct', reason: 'system-no-proxy' }
}
