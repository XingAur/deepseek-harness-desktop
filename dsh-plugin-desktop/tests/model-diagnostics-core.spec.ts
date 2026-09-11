/** Unit coverage for the model-diagnostics core: endpoint shaping, probe retries, error excerpts. */

import { createServer, type Server } from 'node:http'
import { mkdtemp, readFile, writeFile, mkdir } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { modelsEndpoint, probeModels, probeProvider, selectModel } from '../src/model-diagnostics-core.ts'

let server: Server | undefined

afterEach(async () => {
  await new Promise<void>(resolve => {
    if (server === undefined) return resolve()
    server.close(() => resolve())
    server = undefined
  })
})

async function temporaryHome(baseURL: string): Promise<string> {
  const home = await mkdtemp(join(tmpdir(), 'dsh-mdx-'))
  await mkdir(home, { recursive: true })
  await writeFile(join(home, 'settings.yaml'), [
    'agent-default-model:',
    '  provider: deepseek',
    '  model: deepseek-chat',
    'llm-deepseek:',
    `  baseURL: ${baseURL}`,
    '',
  ].join('\n'), 'utf8')
  await writeFile(join(home, '.credentials.yaml'), [
    'version: 1',
    'refs:',
    '  DEEPSEEK_API_KEY: sk-test-key-000000000000000000000000',
    '',
  ].join('\n'), 'utf8')
  return home
}

function listen(server: Server): Promise<string> {
  return new Promise(resolve => {
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (address === null || typeof address === 'string') throw new Error('no listen address')
      resolve(`http://127.0.0.1:${String(address.port)}`)
    })
  })
}

describe('modelsEndpoint', () => {
  it('strips trailing separators and the anthropic-compat suffix', () => {
    expect(modelsEndpoint('https://api.deepseek.com')).toBe('https://api.deepseek.com/models')
    expect(modelsEndpoint('https://api.deepseek.com/')).toBe('https://api.deepseek.com/models')
    expect(modelsEndpoint('https://api.deepseek.com/anthropic')).toBe('https://api.deepseek.com/models')
    expect(modelsEndpoint('https://api.deepseek.com/anthropic/')).toBe('https://api.deepseek.com/models')
  })
})

describe('probeModels', () => {
  it('returns the sorted model list on success', async () => {
    let hits = 0
    server = createServer((req, res) => {
      hits++
      expect(req.url).toBe('/models')
      expect(req.headers.authorization).toBe('Bearer sk-test-key-000000000000000000000000')
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ data: [{ id: 'deepseek-v4-pro' }, { id: 'deepseek-flash' }] }))
    })
    const base = await listen(server)
    const home = await temporaryHome(base)
    await expect(probeModels(home)).resolves.toEqual({
      ok: true,
      status: 200,
      models: ['deepseek-flash', 'deepseek-v4-pro'],
      error: null,
    })
    expect(hits).toBe(1)
  })

  it('fails once for a 4xx with the upstream body excerpt and no retry', async () => {
    let hits = 0
    server = createServer((_req, res) => {
      hits++
      res.writeHead(401, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ error: { message: 'Authentication Fails, key is invalid' } }))
    })
    const base = await listen(server)
    const home = await temporaryHome(base)
    const result = await probeModels(home)
    expect(result.ok).toBe(false)
    expect(result.status).toBe(401)
    expect(result.error).toContain('HTTP 401')
    expect(result.error).toContain('Authentication Fails')
    expect(hits).toBe(1)
  })

  it('retries a 5xx once before reporting the excerpt', async () => {
    const statuses = [502, 200]
    let hits = 0
    server = createServer((_req, res) => {
      const status = statuses[hits] ?? 500
      hits++
      if (status === 200) {
        res.writeHead(200, { 'content-type': 'application/json' })
        res.end(JSON.stringify({ data: [{ id: 'deepseek-flash' }] }))
        return
      }
      res.writeHead(status, { 'content-type': 'text/plain' })
      res.end('bad gateway')
    })
    const base = await listen(server)
    const home = await temporaryHome(base)
    await expect(probeModels(home)).resolves.toMatchObject({ ok: true, models: ['deepseek-flash'] })
    expect(hits).toBe(2)
  })

  it('reports a connection failure without throwing', async () => {
    server = createServer((_req, res) => { res.writeHead(200); res.end('{}') })
    const base = await listen(server)
    await new Promise<void>(resolve => server!.close(() => resolve()))
    const home = await temporaryHome(base)
    const result = await probeModels(home)
    expect(result.ok).toBe(false)
    expect(result.error).not.toBe('')
    expect(result.models).toEqual([])
  })

  it('describes missing configuration explicitly', async () => {
    const empty = await mkdtemp(join(tmpdir(), 'dsh-mdx-empty-'))
    await expect(probeModels(empty)).resolves.toMatchObject({ ok: false, error: 'no baseURL configured' })
    const home = await temporaryHome('https://api.deepseek.com')
    await writeFile(join(home, '.credentials.yaml'), 'version: 1\n', 'utf8')
    await expect(probeModels(home)).resolves.toMatchObject({ ok: false, error: 'no API key stored' })
  })
})

describe('probeProvider', () => {
  async function piAiHome(baseURL: string | null, keyRef?: string): Promise<string> {
    const home = await mkdtemp(join(tmpdir(), 'dsh-mdx-pi-'))
    const provider = baseURL === null ? '' : `    minimax-cn:\n      baseURL: ${baseURL}\n`
    await writeFile(join(home, 'settings.yaml'), `llm-pi-ai:\n  providers:\n${provider}`, 'utf8')
    await writeFile(join(home, '.credentials.yaml'), keyRef === undefined
      ? 'version: 1\n'
      : `version: 1\nrefs:\n  ${keyRef}: sk-pi-key-000000000000000000000\n`, 'utf8')
    return home
  }

  it('probes a configured pi-ai provider with its derived credential ref', async () => {
    server = createServer((req, res) => {
      expect(req.url).toBe('/models')
      expect(req.headers.authorization).toBe('Bearer sk-pi-key-000000000000000000000')
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify({ data: [{ id: 'glm-5' }] }))
    })
    const base = await listen(server)
    const home = await piAiHome(base, 'MINIMAX_CN_API_KEY')
    await expect(probeProvider(home, 'minimax-cn')).resolves.toMatchObject({ ok: true, models: ['glm-5'] })
  })

  it('describes missing baseURL and missing credentials explicitly', async () => {
    await expect(probeProvider(await piAiHome(null), 'minimax-cn')).resolves.toMatchObject({
      ok: false,
      error: 'no baseURL configured for minimax-cn',
    })
    await expect(probeProvider(await piAiHome('https://api.example.com'), 'minimax-cn')).resolves.toMatchObject({
      ok: false,
      error: 'no API key stored for minimax-cn',
    })
  })

  it('rejects malformed provider ids without touching settings', async () => {
    await expect(probeProvider(await piAiHome('https://api.example.com', 'X_API_KEY'), 'not/valid')).resolves.toMatchObject({
      ok: false,
      error: 'invalid provider id',
    })
  })
})

describe('selectModel', () => {
  it('persists the default model alongside the existing provider', async () => {
    const home = await temporaryHome('https://api.deepseek.com')
    await expect(selectModel(home, 'deepseek-flash')).resolves.toEqual({ accepted: true })
    const text = await readFile(join(home, 'settings.yaml'), 'utf8')
    expect(text).toContain('agent-default-model:')
    expect(text).toContain('model: deepseek-flash')
    expect(text).toContain('provider: deepseek')
  })

  it('rejects blank and oversized model ids', async () => {
    const home = await temporaryHome('https://api.deepseek.com')
    await expect(selectModel(home, '')).rejects.toThrow('invalid model id')
    await expect(selectModel(home, 'x'.repeat(201))).rejects.toThrow('invalid model id')
  })
})
