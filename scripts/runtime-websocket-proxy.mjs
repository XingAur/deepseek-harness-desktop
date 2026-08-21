import { connect as connectNet } from 'node:net'

export const RUNTIME_EVENT_PATHS = new Set(['/api/events.mux', '/api/events.host'])

export function attachRuntimeWebSocketProxy(server, { hostname = '127.0.0.1', port }) {
  if (!Number.isInteger(port) || port < 1 || port > 65_535) {
    throw new TypeError('Runtime WebSocket proxy port must be a valid TCP port')
  }

  const activeSockets = new Set()
  const onUpgrade = (request, clientSocket, head) => {
    const pathname = safePathname(request.url)
    if (!RUNTIME_EVENT_PATHS.has(pathname)) {
      rejectUpgrade(clientSocket, 404, 'Not Found')
      return
    }

    let connected = false
    const upstreamSocket = connectNet({ host: hostname, port })
    activeSockets.add(clientSocket)
    activeSockets.add(upstreamSocket)
    upstreamSocket.setTimeout(5_000, () => upstreamSocket.destroy(new Error('Runtime WebSocket upstream timed out')))

    upstreamSocket.once('connect', () => {
      connected = true
      upstreamSocket.setTimeout(0)
      upstreamSocket.write(serializeRequest(request))
      if (head.length > 0) upstreamSocket.write(head)
      clientSocket.pipe(upstreamSocket)
      upstreamSocket.pipe(clientSocket)
    })
    upstreamSocket.once('error', () => {
      if (!connected) rejectUpgrade(clientSocket, 503, 'Service Unavailable')
      else clientSocket.destroy()
    })
    clientSocket.once('error', () => upstreamSocket.destroy())
    clientSocket.once('close', () => {
      activeSockets.delete(clientSocket)
      upstreamSocket.destroy()
    })
    upstreamSocket.once('close', () => {
      activeSockets.delete(upstreamSocket)
    })
  }

  server.on('upgrade', onUpgrade)
  return () => {
    server.off('upgrade', onUpgrade)
    for (const socket of activeSockets) socket.destroy()
    activeSockets.clear()
  }
}

function safePathname(value) {
  try {
    return new URL(value ?? '/', 'http://127.0.0.1').pathname
  } catch {
    return ''
  }
}

function serializeRequest(request) {
  const requestLine = `${request.method ?? 'GET'} ${request.url ?? '/'} HTTP/${request.httpVersion}\r\n`
  const headers = []
  for (let index = 0; index < request.rawHeaders.length; index += 2) {
    headers.push(`${request.rawHeaders[index]}: ${request.rawHeaders[index + 1]}`)
  }
  return `${requestLine}${headers.join('\r\n')}\r\n\r\n`
}

function rejectUpgrade(socket, status, reason) {
  if (socket.destroyed) return
  socket.end([
    `HTTP/1.1 ${status} ${reason}`,
    'Connection: close',
    'Content-Length: 0',
    '',
    '',
  ].join('\r\n'))
}
