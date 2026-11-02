import type { IncomingMessage, ServerResponse } from 'node:http'

export function writeJson(res: ServerResponse, status: number, value: unknown): void {
  const body = `${JSON.stringify(value)}\n`
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
    'x-content-type-options': 'nosniff',
    'content-length': String(Buffer.byteLength(body)),
  })
  res.end(body)
}

export function sameOriginMutatingRequest(req: IncomingMessage): boolean {
  const origin = req.headers.origin
  const referer = req.headers.referer
  const host = req.headers.host ?? ''
  // Official desktop forwards dsh-app fetches with Origin stripped; the Host
  // cookie (requestRejection) is what authenticates those requests.
  if (origin === undefined || origin === '') {
    if (typeof referer === 'string' && referer.startsWith('dsh-app:')) return true
    return true
  }
  try {
    const parsed = new URL(origin)
    if (parsed.protocol === 'dsh-app:') return true
    return parsed.host !== '' && parsed.host === host
  } catch {
    return false
  }
}

const MAX_BODY_BYTES = 64 * 1024

export async function readJsonBody(req: IncomingMessage): Promise<Record<string, unknown>> {
  const chunks: Buffer[] = []
  let size = 0
  for await (const chunk of req) {
    size += (chunk as Buffer).length
    if (size > MAX_BODY_BYTES) throw new Error('request body too large')
    chunks.push(chunk as Buffer)
  }
  if (chunks.length === 0) return {}
  const parsed: unknown = JSON.parse(Buffer.concat(chunks).toString('utf8'))
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    throw new Error('request body must be a JSON object')
  }
  return parsed as Record<string, unknown>
}

export function rejectUnauthorized(
  req: IncomingMessage,
  res: ServerResponse,
  rejection: number | undefined,
): boolean {
  if (rejection === undefined) return false
  res.writeHead(rejection)
  res.end(rejection === 401 ? 'unauthorized' : 'forbidden')
  return true
}
