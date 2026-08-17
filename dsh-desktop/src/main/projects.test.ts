import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { listProjects } from './projects.js'

const dirs: string[] = []
afterEach(() => { for (const d of dirs) rmSync(d, { recursive: true, force: true }); dirs.length = 0 })
const tmp = () => { const d = mkdtempSync(join(tmpdir(), 'proj-')); dirs.push(d); return d }

function mkProject(projectsDir: string, id: string, json: unknown, files: string[] = ['index.html']): void {
  const dir = join(projectsDir, id)
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, 'project.json'), typeof json === 'string' ? json : JSON.stringify(json))
  for (const f of files) writeFileSync(join(dir, f), 'x')
}

describe('listProjects', () => {
  it('目录不存在 → 空数组', () => {
    expect(listProjects(join(tmp(), 'nope'))).toEqual([])
  })
  it('合法静态项目被列出', () => {
    const dir = tmp()
    mkProject(dir, 'a', { name: 'A 工作台', icon: 'box', desc: 'd', entry: 'index.html' })
    const list = listProjects(dir)
    expect(list.length).toBe(1)
    expect(list[0].name).toBe('A 工作台')
    expect(list[0].start).toBeUndefined()
  })
  it('start 项目必须有 port，否则跳过', () => {
    const dir = tmp()
    mkProject(dir, 'ok', { name: 'S', entry: 'index.html', start: 'node server.js', port: 8801 })
    mkProject(dir, 'bad', { name: 'B', entry: 'index.html', start: 'node s.js' })
    const list = listProjects(dir)
    expect(list.length).toBe(1)
    expect(list[0].port).toBe(8801)
  })
  it('非法 JSON 与缺 name/entry 的目录被跳过', () => {
    const dir = tmp()
    mkProject(dir, 'broken', '{oops')
    mkProject(dir, 'noname', { entry: 'index.html' })
    expect(listProjects(dir)).toEqual([])
  })
})
