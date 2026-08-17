import { existsSync, mkdtempSync, readdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { createLogger } from './logger.js'

const dirs: string[] = []
afterEach(() => { for (const d of dirs) rmSync(d, { recursive: true, force: true }); dirs.length = 0 })
const tmp = () => { const d = mkdtempSync(join(tmpdir(), 'log-')); dirs.push(d); return d }
const today = () => new Date().toISOString().slice(0, 10)

describe('createLogger', () => {
  it('写入当天日志文件', () => {
    const dir = tmp()
    const log = createLogger(dir, 'main', 7)
    log('hello')
    const f = join(dir, `main-${today()}.log`)
    expect(existsSync(f)).toBe(true)
    expect(readFileSync(f, 'utf8')).toContain('hello')
  })
  it('只保留最近 N 天', () => {
    const dir = tmp()
    for (let i = 1; i <= 10; i++) writeFileSync(join(dir, `main-2026-01-${String(i).padStart(2, '0')}.log`), '')
    createLogger(dir, 'main', 7)
    const files = readdirSync(dir).filter(f => f.startsWith('main-'))
    expect(files.length).toBe(7)
  })
})
