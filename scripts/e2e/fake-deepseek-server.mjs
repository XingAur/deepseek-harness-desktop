import { startLoopbackHttps } from './tls-fixture.mjs'

export async function startFakeDeepSeek(options = {}) {
  const text = options.text ?? 'E2E_PONG'
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

    if (request.method !== 'POST' || url.pathname !== '/chat/completions') {
      response.writeHead(404).end('not found')
      return
    }

    response.writeHead(200, {
      'cache-control': 'no-cache',
      connection: 'keep-alive',
      'content-type': 'text/event-stream; charset=utf-8',
    })
    const split = Math.max(1, Math.floor(text.length / 2))
    response.write(sseChunk(text.slice(0, split), null))
    response.write(sseChunk(text.slice(split), 'stop'))
    response.end('data: [DONE]\n\n')
  }, options.tls)
  origin = listener.url

  return Object.freeze({
    url: listener.url,
    caCertificate: listener.tls.caCertificate,
    requests: () => requests.slice(),
    clearRequests: () => { requests.length = 0 },
    close: listener.close,
  })
}

function sseChunk(content, finishReason) {
  return `data: ${JSON.stringify({
    id: 'chatcmpl-e2e',
    object: 'chat.completion.chunk',
    choices: [{ index: 0, delta: { content }, finish_reason: finishReason }],
  })}\n\n`
}

function readBody(request) {
  return new Promise((resolve, reject) => {
    const chunks = []
    request.on('data', (chunk) => chunks.push(Buffer.from(chunk)))
    request.on('end', () => resolve(Buffer.concat(chunks).toString()))
    request.on('error', reject)
  })
}
