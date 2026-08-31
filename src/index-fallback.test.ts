import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

describe('desktop HTML bootstrap fallback', () => {
  it('keeps a visible loading surface before React mounts', () => {
    const html = readFileSync(resolve(process.cwd(), 'index.html'), 'utf8')

    expect(html).toContain('id="bootstrap-fallback"')
    expect(html).toContain('正在加载 DeepSeek Harness')
    expect(html).toContain('background:#111113')
  })
})
