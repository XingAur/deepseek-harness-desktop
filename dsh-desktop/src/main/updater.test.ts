import { existsSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchLatest, installUserVersion } from './updater.js'

const dirs: string[] = []
afterEach(() => { for (const d of dirs) rmSync(d, { recursive: true, force: true }); dirs.length = 0; vi.restoreAllMocks() })
const tmp = () => { const d = mkdtempSync(join(tmpdir(), 'upd-')); dirs.push(d); return d }

describe('fetchLatest', () => {
  it('npmmirror 成功 → 返回版本与 tarball', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      json: async () => ({ version: '0.2.0', dist: { tarball: 'https://r/t.tgz' } }),
    })))
    const info = await fetchLatest()
    expect(info).toEqual({ version: '0.2.0', tarball: 'https://r/t.tgz' })
  })
  it('npmmirror 失败回退 npmjs', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: false })
      .mockResolvedValueOnce({ ok: true, json: async () => ({ version: '0.3.0', dist: { tarball: 'https://n/t.tgz' } }) })
    vi.stubGlobal('fetch', fetchMock)
    expect((await fetchLatest()).version).toBe('0.3.0')
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
  it('全部失败 → REGISTRY_UNREACHABLE', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false })))
    await expect(fetchLatest()).rejects.toThrow('REGISTRY_UNREACHABLE')
  })
})

describe('installUserVersion', () => {
  it('调用 pnpm add 并产出目录', async () => {
    const runtime = join(tmp(), 'runtime', 'dsh')
    const run = vi.fn((args: string[], cwd: string) => {
      expect(args[0]).toBe('add')
      expect(args[1]).toBe('@deepseek-ai/dsh@0.2.0')
      expect(cwd.startsWith(runtime)).toBe(true)
      return { status: 0 }
    })
    const dest = await installUserVersion(runtime, '0.2.0', { runPnpm: run })
    expect(existsSync(dest)).toBe(true)
  })
  it('pnpm 失败 → INSTALL_FAILED 且清理目录', async () => {
    const runtime = join(tmp(), 'runtime', 'dsh')
    await expect(installUserVersion(runtime, '0.2.0', { runPnpm: () => ({ status: 1 }) }))
      .rejects.toThrow('INSTALL_FAILED')
  })
  it('已存在则幂等返回', async () => {
    const runtime = join(tmp(), 'runtime', 'dsh')
    const run = vi.fn((_args: string[], cwd: string) => {
      mkdirSync(join(cwd, 'node_modules', '@deepseek-ai', 'dsh'), { recursive: true })
      return { status: 0 }
    })
    await installUserVersion(runtime, '0.2.0', { runPnpm: run })
    await installUserVersion(runtime, '0.2.0', { runPnpm: run })
    expect(run).toHaveBeenCalledTimes(1)
  })
})
