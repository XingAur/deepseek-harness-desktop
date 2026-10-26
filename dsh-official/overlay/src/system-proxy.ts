/** Read-only system proxy discovery; no port probing, environment fallbacks or settings changes. */

import { execFile } from 'node:child_process'
import { isIP } from 'node:net'
import { join } from 'node:path'
import { parseProxyBypass } from './model-proxy-routing.ts'

export type SystemProxyStatus = 'ready' | 'direct' | 'unsupported' | 'error'
export interface SystemProxySnapshot {
  readonly status: SystemProxyStatus
  readonly httpProxy: string
  readonly httpsProxy: string
  readonly bypass: readonly string[]
  readonly error?: string
}
export type SystemProxyRunner = (command: string, args: readonly string[]) => Promise<string>
export interface SystemProxyOptions {
  readonly platform?: string
  readonly runner?: SystemProxyRunner
}

type ScutilValue = string | { [key: string]: ScutilValue }
type ScutilRecord = { [key: string]: ScutilValue }
const MAX_SCUTIL_BYTES = 256 * 1024

function result(status: SystemProxyStatus, fields: Partial<Omit<SystemProxySnapshot, 'status'>> = {}): SystemProxySnapshot {
  return Object.freeze({
    status, httpProxy: '', httpsProxy: '', ...fields,
    bypass: Object.freeze([...(fields.bypass ?? [])]),
  })
}

function fail(code = 'system-proxy-invalid-config'): never { throw new Error(code) }
function invalid(): never { return fail() }
function record(value: ScutilValue | undefined): ScutilRecord {
  if (value === undefined) return {}
  if (typeof value === 'string') return invalid()
  return value
}

/** Parse the documented scutil dictionary shape without evaluating any output. */
function scutilDictionary(output: string): ScutilRecord {
  if (!output || Buffer.byteLength(output) > MAX_SCUTIL_BYTES) return invalid()
  const lines = output.trim().split(/\r?\n/).map(line => line.trim()).filter(Boolean)
  if (lines.length === 1 && /^<dictionary>\s*\{\s*\}$/.test(lines[0]!)) return {}
  if (lines[0] !== '<dictionary> {') return invalid()
  const root: ScutilRecord = Object.create(null)
  const stack = [root]
  let closed = false
  for (const line of lines.slice(1)) {
    if (closed) return invalid()
    if (line === '}') {
      stack.pop()
      if (stack.length === 0) closed = true
      continue
    }
    const match = /^([^:]+?)\s*:\s*(.*)$/.exec(line)
    const parent = stack.at(-1)
    if (!match || !parent) return invalid()
    const key = match[1]!.trim()
    const value = match[2]!.trim()
    if (!key || Object.hasOwn(parent, key)) return invalid()
    if (/^<(?:dictionary|array)>\s*\{$/.test(value)) {
      const child: ScutilRecord = Object.create(null)
      parent[key] = child
      stack.push(child)
    } else if (/^<(?:dictionary|array)>\s*\{\s*\}$/.test(value)) {
      parent[key] = Object.create(null)
    } else if (value.startsWith('<') || value === '{') {
      return invalid()
    } else {
      parent[key] = value
    }
  }
  if (!closed || stack.length !== 0) return invalid()
  return root
}

function enabled(source: ScutilRecord, key: string): boolean {
  const value = source[key]
  if (value === undefined || value === '0') return false
  if (value === '1') return true
  return invalid()
}

function proxyAddress(source: ScutilRecord, prefix: 'HTTP' | 'HTTPS'): string {
  if (!enabled(source, `${prefix}Enable`)) return ''
  const host = source[`${prefix}Proxy`]
  const port = source[`${prefix}Port`]
  if (typeof host !== 'string' || typeof port !== 'string' || !/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535) return invalid()
  if (!host || /[\s/@\\?#]/.test(host)) return invalid()
  if ((host.startsWith('[') || host.endsWith(']')) && !/^\[[^\]]+\]$/.test(host)) return invalid()
  const bareHost = host.replace(/^\[|\]$/g, '')
  if (bareHost.includes(':') && isIP(bareHost) !== 6) return invalid()
  const address = `http://${isIP(bareHost) === 6 ? `[${bareHost}]` : bareHost}:${Number(port)}`
  let url: URL
  try { url = new URL(address) } catch { return invalid() }
  if (!url.hostname || url.username || url.password || url.search || url.hash || url.pathname !== '/') return invalid()
  // Secure Web Proxy (HTTPS) describes the destination, not TLS to the proxy.
  return url.origin
}

function exceptionList(source: ScutilRecord): readonly string[] {
  const exceptions = record(source.ExceptionsList)
  if (Object.keys(exceptions).some(key => !/^\d+$/.test(key))) return invalid()
  const values = Object.keys(exceptions).sort((a, b) => Number(a) - Number(b)).map(key => exceptions[key])
  if (values.some(value => typeof value !== 'string')) return invalid()
  if (enabled(source, 'ExcludeSimpleHostnames')) values.push('<local>')
  try { return parseProxyBypass(values, 'system') } catch { return invalid() }
}

export function parseScutilProxyOutput(output: string): SystemProxySnapshot {
  try {
    const source = scutilDictionary(output)
    if (enabled(source, 'ProxyAutoConfigEnable')) return result('unsupported', { error: 'system-proxy-pac-unsupported' })
    if (enabled(source, 'ProxyAutoDiscoveryEnable')) return result('unsupported', { error: 'system-proxy-autodiscovery-unsupported' })
    // Per-interface / per-domain policy requires destination-specific OS APIs.
    // Do not pretend that reading only the global dictionary implements it.
    if (Object.keys(record(source.__SCOPED__)).length || Object.keys(record(source.__SUPPLEMENTAL__)).length) {
      return result('unsupported', { error: 'system-proxy-scoped-unsupported' })
    }
    const httpProxy = proxyAddress(source, 'HTTP')
    const httpsProxy = proxyAddress(source, 'HTTPS')
    if (enabled(source, 'SOCKSEnable') && (!httpProxy || !httpsProxy)) {
      return result('unsupported', { error: 'system-proxy-socks-unsupported' })
    }
    const bypass = exceptionList(source)
    return result(httpProxy || httpsProxy ? 'ready' : 'direct', { httpProxy, httpsProxy, bypass })
  } catch {
    return result('error', { error: 'system-proxy-invalid-config' })
  }
}

const WINDOWS_KNOWN = new Set([
  'system-proxy-invalid-config', 'system-proxy-pac-unsupported', 'system-proxy-autodiscovery-unsupported',
  'system-proxy-socks-unsupported', 'system-proxy-list-unsupported', 'system-proxy-bypass-unsupported',
])

/** Current-user IE/WinHTTP proxy fields only. PAC URLs are never copied into the snapshot. */
export interface WindowsIeProxyRaw {
  readonly schemaVersion: 1
  readonly ok: boolean
  readonly autoDetect?: boolean
  readonly autoConfigUrl?: string
  readonly proxy?: string
  readonly proxyBypass?: string
  readonly autoDetectFound?: boolean
  readonly autoDetectError?: 'not-found' | 'error' | ''
}

function windowsEndpoint(value: string): string {
  let input = value.trim()
  if (!input) return invalid()
  if (/^https:\/\//i.test(input)) return fail('system-proxy-list-unsupported')
  if (/^http:\/\//i.test(input)) input = input.slice('http://'.length)
  if (/[\s/@\\?#]/.test(input)) return invalid()
  let host: string
  let portText: string
  if (input.startsWith('[')) {
    const match = /^\[([^\]]+)\](?::(\d+))?$/.exec(input)
    if (!match) return invalid()
    host = match[1]!
    portText = match[2] || '80'
  } else {
    const index = input.lastIndexOf(':')
    if (index === -1) {
      host = input
      portText = '80'
    } else {
      host = input.slice(0, index)
      portText = input.slice(index + 1)
    }
  }
  if (!/^\d+$/.test(portText) || Number(portText) < 1 || Number(portText) > 65535) return invalid()
  if (!host || (host.includes(':') && isIP(host) !== 6)) return invalid()
  const address = `http://${isIP(host) === 6 ? `[${host}]` : host}:${Number(portText)}`
  let url: URL
  try { url = new URL(address) } catch { return invalid() }
  if (!url.hostname || url.username || url.password || url.search || url.hash || url.pathname !== '/') return invalid()
  return url.origin
}

function parseWindowsProxyServer(raw: string): { httpProxy: string; httpsProxy: string } {
  const trimmed = raw.trim()
  if (!trimmed) return { httpProxy: '', httpsProxy: '' }
  const tokens = trimmed.split(/[;\s]+/u).map(token => token.trim()).filter(Boolean)
  if (tokens.length === 0) return { httpProxy: '', httpsProxy: '' }
  let fallback = ''
  const mapped: { http?: string; https?: string; socks?: string } = {}
  for (const token of tokens) {
    const separator = token.indexOf('=')
    if (separator === -1) {
      if (fallback) return fail('system-proxy-list-unsupported')
      fallback = windowsEndpoint(token)
      continue
    }
    const key = token.slice(0, separator).toLowerCase()
    const value = token.slice(separator + 1)
    if (key === 'ftp') continue
    if (key !== 'http' && key !== 'https' && key !== 'socks') return fail('system-proxy-list-unsupported')
    if (Object.hasOwn(mapped, key)) return fail('system-proxy-list-unsupported')
    if (key === 'socks') {
      mapped.socks = windowsEndpoint(value)
      continue
    }
    mapped[key] = windowsEndpoint(value)
  }
  const httpProxy = mapped.http ?? fallback
  const httpsProxy = mapped.https ?? fallback
  if (mapped.socks && (!httpProxy || !httpsProxy)) return fail('system-proxy-socks-unsupported')
  return { httpProxy, httpsProxy }
}

function parseWindowsProxyBypass(raw: string): readonly string[] {
  const tokens = raw.split(';').map(token => token.trim()).filter(Boolean)
  if (tokens.some(token => token.toLowerCase() === '<-loopback>' || /^<.+>$/u.test(token) && token.toLowerCase() !== '<local>')) {
    return fail('system-proxy-bypass-unsupported')
  }
  try { return parseProxyBypass(tokens, 'system') } catch { return invalid() }
}

export function parseWindowsIeProxyOutput(output: string): SystemProxySnapshot {
  try {
    if (!output || Buffer.byteLength(output) > MAX_SCUTIL_BYTES) return invalid()
    const parsed: unknown = JSON.parse(output)
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) return invalid()
    const source = parsed as Record<string, unknown>
    if (source.schemaVersion !== 1 || source.ok !== true) return result('error', { error: 'system-proxy-read-failed' })
    if (typeof source.autoDetect !== 'boolean' || typeof source.autoConfigUrl !== 'string'
      || typeof source.proxy !== 'string' || typeof source.proxyBypass !== 'string') return invalid()
    if (source.autoConfigUrl.trim()) return result('unsupported', { error: 'system-proxy-pac-unsupported' })
    if (source.autoDetect) {
      if (source.autoDetectError === 'error') return result('error', { error: 'system-proxy-read-failed' })
      if (source.autoDetectFound === true) return result('unsupported', { error: 'system-proxy-autodiscovery-unsupported' })
      if (source.autoDetectFound !== false || source.autoDetectError !== 'not-found') return invalid()
    } else if (source.autoDetectFound === true || (source.autoDetectError && source.autoDetectError !== '')) {
      return invalid()
    }
    const { httpProxy, httpsProxy } = parseWindowsProxyServer(source.proxy)
    const bypass = parseWindowsProxyBypass(source.proxyBypass)
    return result(httpProxy || httpsProxy ? 'ready' : 'direct', { httpProxy, httpsProxy, bypass })
  } catch (cause) {
    const code = cause instanceof Error ? cause.message : ''
    if (WINDOWS_KNOWN.has(code)) return result(code.endsWith('unsupported') ? 'unsupported' : 'error', { error: code })
    return result('error', { error: 'system-proxy-invalid-config' })
  }
}

const WINDOWS_IE_PROXY_SCRIPT = `
$ErrorActionPreference = 'Stop'
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class DshWinHttpProxy {
  [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
  public struct Config {
    public bool AutoDetect;
    public IntPtr AutoConfigUrl;
    public IntPtr Proxy;
    public IntPtr ProxyBypass;
  }
  [DllImport("winhttp.dll", SetLastError = true)]
  public static extern bool WinHttpGetIEProxyConfigForCurrentUser(out Config config);
  [DllImport("winhttp.dll", SetLastError = true, CharSet = CharSet.Unicode)]
  public static extern bool WinHttpDetectAutoProxyConfigUrl(int flags, out IntPtr url);
  [DllImport("kernel32.dll")]
  public static extern IntPtr GlobalFree(IntPtr hMem);
  public static string TakeString(IntPtr pointer) {
    if (pointer == IntPtr.Zero) return "";
    try { return Marshal.PtrToStringUni(pointer) ?? ""; }
    finally { GlobalFree(pointer); }
  }
}
'@
function Emit($payload) { [Console]::Out.Write(($payload | ConvertTo-Json -Compress)) }
$config = New-Object DshWinHttpProxy+Config
if (-not [DshWinHttpProxy]::WinHttpGetIEProxyConfigForCurrentUser([ref]$config)) {
  Emit @{ schemaVersion = 1; ok = $false }
  exit 1
}
$autoConfigUrl = [DshWinHttpProxy]::TakeString($config.AutoConfigUrl)
$payload = @{
  schemaVersion = 1
  ok = $true
  autoDetect = [bool]$config.AutoDetect
  autoConfigUrl = $autoConfigUrl
  proxy = [DshWinHttpProxy]::TakeString($config.Proxy)
  proxyBypass = [DshWinHttpProxy]::TakeString($config.ProxyBypass)
  autoDetectFound = $false
  autoDetectError = ''
}
if ($payload.autoDetect -and -not $autoConfigUrl) {
  $discovered = [IntPtr]::Zero
  if ([DshWinHttpProxy]::WinHttpDetectAutoProxyConfigUrl(3, [ref]$discovered)) {
    [void][DshWinHttpProxy]::TakeString($discovered)
    $payload.autoDetectFound = $true
  } else {
    $code = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
    if ($code -eq 12180) { $payload.autoDetectError = 'not-found' }
    else { $payload.autoDetectError = 'error' }
  }
}
Emit $payload
`.trim()

function windowsPowershellPath(): string {
  const root = process.env.SystemRoot
  if (root && /^[A-Za-z]:[\\/][^.]/u.test(root) && !root.includes('..')) {
    return join(root, 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
  }
  return join('C:\\Windows', 'System32', 'WindowsPowerShell', 'v1.0', 'powershell.exe')
}

function windowsEncodedCommand(): string {
  return Buffer.from(WINDOWS_IE_PROXY_SCRIPT, 'utf16le').toString('base64')
}

const runSystemCommand: SystemProxyRunner = (command, args) => new Promise((resolve, reject) => {
  const timeout = command.endsWith('powershell.exe') ? 8_000 : 3_000
  execFile(command, [...args], {
    encoding: 'utf8', timeout, maxBuffer: MAX_SCUTIL_BYTES, windowsHide: true,
  }, (error, stdout) => {
    if (error) reject(new Error('system-proxy-read-failed'))
    else resolve(stdout)
  })
})

/** macOS scutil and Windows current-user WinHTTP static HTTP(S) settings. */
export async function readSystemProxy(options: SystemProxyOptions = {}): Promise<SystemProxySnapshot> {
  const platform = options.platform ?? process.platform
  const runner = options.runner ?? runSystemCommand
  try {
    if (platform === 'darwin') {
      return parseScutilProxyOutput(await runner('/usr/sbin/scutil', ['--proxy']))
    }
    if (platform === 'win32') {
      return parseWindowsIeProxyOutput(await runner(windowsPowershellPath(), [
        '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-EncodedCommand', windowsEncodedCommand(),
      ]))
    }
    return result('unsupported', { error: 'system-proxy-unsupported-platform' })
  } catch {
    // Never return stdout/stderr or an arbitrary command exception to the UI.
    return result('error', { error: 'system-proxy-read-failed' })
  }
}
