import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const dir = dirname(fileURLToPath(import.meta.url))
createServer(async (_req, res) => {
  res.setHeader('content-type', 'text/html; charset=utf-8')
  res.end(await readFile(join(dir, 'index.html'), 'utf8'))
}).listen(8801, '127.0.0.1', () => console.log('hello-start on 8801'))
