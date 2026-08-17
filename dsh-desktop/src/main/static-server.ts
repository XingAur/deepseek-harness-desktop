import { createReadStream, existsSync, statSync } from 'node:fs'
import http from 'node:http'
import { extname, join, normalize, sep } from 'node:path'

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript',
  '.mjs': 'text/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
}

export interface StaticServer { port: number; close(): Promise<void> }

export function startStaticServer(rootDir: string): Promise<StaticServer> {
  return new Promise((resolve, reject) => {
    const server = http.createServer((req, res) => serve(rootDir, req, res))
    server.on('error', reject)
    server.listen(0, '127.0.0.1', () => {
      const port = (server.address() as { port: number }).port
      resolve({
        port,
        close: () => new Promise<void>(done => server.close(() => done())),
      })
    })
  })
}

function serve(rootDir: string, req: http.IncomingMessage, res: http.ServerResponse): void {
  if (req.method !== 'GET' && req.method !== 'HEAD') { res.writeHead(405).end(); return }
  const url = new URL(req.url ?? '/', 'http://x')
  let rel = decodeURIComponent(url.pathname)
  if (rel.endsWith('/')) rel += 'index.html'
  const full = normalize(join(rootDir, rel))
  if (!full.startsWith(normalize(rootDir) + sep)) { res.writeHead(403).end(); return }
  if (!existsSync(full) || !statSync(full).isFile()) { res.writeHead(404).end(); return }
  res.writeHead(200, { 'content-type': MIME[extname(full).toLowerCase()] ?? 'application/octet-stream' })
  createReadStream(full).pipe(res)
}
