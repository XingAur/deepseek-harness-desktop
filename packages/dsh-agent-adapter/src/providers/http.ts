import { classifyProviderFailure, type ProviderRequestError } from './error-classification.js'

export interface ProviderHttpOptions {
  url: string
  headers: Record<string, string>
  body: string
  signal?: AbortSignal
}

export async function openProviderStream(options: ProviderHttpOptions): Promise<Response> {
  assertProviderUrl(options.url)
  let response: Response
  try {
    response = await fetch(options.url, {
      method: 'POST',
      headers: options.headers,
      body: options.body,
      redirect: 'error',
      signal: options.signal,
    })
  } catch (cause) {
    if (options.signal?.aborted) throw classifyProviderFailure({ cancelled: true })
    throw classifyProviderFailure({ cause })
  }
  if (!response.ok) {
    throw classifyProviderFailure({ status: response.status, retryAfterMs: parseRetryAfter(response.headers.get('retry-after')) })
  }
  return response
}

export async function* readSseEvents(body: ReadableStream<Uint8Array> | null): AsyncGenerator<{ event?: string; data: string }, void, void> {
  if (body === null) throw classifyProviderFailure({ malformedStream: true })
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const next = await reader.read()
      buffer += decoder.decode(next.value ?? new Uint8Array(), { stream: !next.done })
      let separator = findSeparator(buffer)
      while (separator !== -1) {
        const block = buffer.slice(0, separator)
        buffer = buffer.slice(separator + separatorLength(buffer, separator))
        const parsed = parseSseBlock(block)
        if (parsed !== null) yield parsed
        separator = findSeparator(buffer)
      }
      if (next.done) break
    }
    const trailing = parseSseBlock(buffer)
    if (trailing !== null) yield trailing
  } catch (cause) {
    if (isProviderRequestError(cause)) throw cause
    throw classifyProviderFailure({ malformedStream: true })
  } finally {
    reader.releaseLock()
  }
}

export function assertProviderUrl(value: string, allowLoopback = false): URL {
  let url: URL
  try {
    url = new URL(value)
  } catch {
    throw classifyProviderFailure({ redirectRejected: true })
  }
  const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(url.hostname)
  if (url.username !== '' || url.password !== '' || url.hash !== '' || (url.protocol !== 'https:' && !(allowLoopback && loopback && url.protocol === 'http:'))) {
    throw classifyProviderFailure({ redirectRejected: true })
  }
  return url
}

function findSeparator(value: string): number {
  const lf = value.indexOf('\n\n')
  const crlf = value.indexOf('\r\n\r\n')
  if (lf === -1) return crlf
  if (crlf === -1) return lf
  return Math.min(lf, crlf)
}

function separatorLength(value: string, index: number): number {
  return value.startsWith('\r\n\r\n', index) ? 4 : 2
}

function parseSseBlock(block: string): { event?: string; data: string } | null {
  const lines = block.split(/\r?\n/)
  const data = lines.filter((line) => line.startsWith('data:')).map((line) => line.slice(5).trimStart()).join('\n')
  if (data === '') return null
  const event = lines.find((line) => line.startsWith('event:'))?.slice(6).trim()
  return event === undefined || event === '' ? { data } : { event, data }
}

function parseRetryAfter(value: string | null): number | undefined {
  if (value === null) return undefined
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed * 1000 : undefined
}

function isProviderRequestError(value: unknown): value is ProviderRequestError {
  return value instanceof Error && value.name === 'ProviderRequestError'
}
