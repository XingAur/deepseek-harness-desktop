import { describe, expect, it } from 'vitest'
import { existsSync, readFileSync } from 'node:fs'

// 锁定目录快照的形状：打包资源损坏时在 CI 就炸，而不是用户机器上。
describe('bundled plugin catalog snapshot', () => {
  const path = 'plugin-catalog/plugins.json'

  it('ships a schema-1 snapshot with sanitized entries', () => {
    expect(existsSync(path)).toBe(true)
    const snapshot = JSON.parse(readFileSync(path, 'utf8')) as {
      schemaVersion: number
      count: number
      entries: Array<{ id: string; repo: string; category: string; tarball?: string }>
    }
    expect(snapshot.schemaVersion).toBe(1)
    expect(snapshot.count).toBe(snapshot.entries.length)
    expect(snapshot.count).toBeGreaterThan(1000)
    for (const entry of snapshot.entries.slice(0, 200)) {
      expect(entry.id).toMatch(/^[A-Za-z0-9][A-Za-z0-9._/-]*$/)
      expect(entry.repo).toBe(`https://github.com/${entry.id}`)
      expect(entry.category.length).toBeGreaterThan(0)
      if (entry.tarball !== undefined) {
        expect(entry.tarball).toMatch(/^https:\/\/github\.com\/[^/]+\/[^/]+\/releases\/(latest\/)?download\//)
      }
    }
  })
})
