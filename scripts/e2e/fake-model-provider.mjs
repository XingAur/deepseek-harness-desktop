import { startLoopbackHttps } from './tls-fixture.mjs'

const DEFAULT_TEXT = 'FAKE_MODEL_REPLY'

/**
 * A deterministic, loopback-only provider fixture. It never authenticates or
 * persists the supplied request body beyond the in-memory request log.
 */
export async function startFakeModelProvider(options = {}) {
  const family = options.family ?? 'openai'
  const text = options.text ?? DEFAULT_TEXT
  const status = options.status ?? 200
  const requests = []
  let origin = ''

  const listener = await startLoopbackHttps(async (request, response) => {
    const url = new URL(request.url ?? '/', origin)
    const body = await readBody(request)
    requests.push(Object.freeze({
      method: request.method ?? 'GET',
      path: url.pathname,
      body,
      at: new Date().toISOString(),
    }))

    if (request.method !== 'POST' || !isSupportedPath(family, url.pathname)) {
      response.writeHead(404).end('not found')
      return
    }
    if (status !== 200) {
      response.writeHead(status, { 'content-type': 'application/json' }).end(JSON.stringify({ error: { message: errorMessage(status) } }))
      return
    }

    response.writeHead(200, {
      'cache-control': 'no-cache',
      connection: 'keep-alive',
      'content-type': 'text/event-stream; charset=utf-8',
    })
    if (family === 'claude') {
      writeClaudeStream(response, text, options.includeUsage ?? true)
    } else {
      writeOpenAIStream(response, text, options.includeUsage ?? true)
    }
  }, options.tls)
  origin = listener.url

  return Object.freeze({
    family,
    url: listener.url,
    caCertificate: listener.tls.caCertificate,
    requests: () => requests.slice(),
    clearRequests: () => { requests.length = 0 },
    close: listener.close,
  })
}

function isSupportedPath(family, pathname) {
  return family === 'claude' ? pathname === '/v1/messages' : pathname === '/chat/completions'
}

function writeOpenAIStream(response, text, includeUsage) {
  const split = Math.max(1, Math.floor(text.length / 2))
  response.write(sseChunk({ choices: [{ index: 0, delta: { content: text.slice(0, split) }, finish_reason: null }] }))
  response.write(sseChunk({ choices: [{ index: 0, delta: { content: text.slice(split) }, finish_reason: 'stop' }], ...(includeUsage ? { usage: { prompt_tokens: 3, completion_tokens: text.length } } : {}) }))
  response.end('data: [DONE]\n\n')
}

function writeClaudeStream(response, text, includeUsage) {
  response.write('event: message_start\ndata: {"type":"message_start","message":{"id":"fake-message"}}\n\n')
  response.write(`event: content_block_delta\ndata: ${JSON.stringify({ type: 'content_block_delta', delta: { type: 'text_delta', text } })}\n\n`)
  response.write(`event: message_delta\ndata: ${JSON.stringify({ type: 'message_delta', delta: { stop_reason: 'end_turn' }, ...(includeUsage ? { usage: { output_tokens: text.length } } : {}) })}\n\n`)
  response.end('event: message_stop\ndata: {}\n\n')
}

function sseChunk(value) {
  return `data: ${JSON.stringify(value)}\n\n`
}

function errorMessage(status) {
  if (status === 401 || status === 403) return 'invalid api key'
  if (status === 402) return 'quota exhausted'
  if (status === 404) return 'unknown model'
  if (status === 429) return 'rate limited'
  return 'provider failure'
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    const chunks = []
    request.on('data', (chunk) => chunks.push(Buffer.from(chunk)))
    request.on('end', () => resolve(Buffer.concat(chunks).toString()))
    request.on('error', reject)
  })
}
