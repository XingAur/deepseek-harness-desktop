import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

export interface Project {
  dir: string
  name: string
  icon: string
  desc: string
  entry: string
  start?: string
  port?: number
}

export function listProjects(projectsDir: string): Project[] {
  if (!existsSync(projectsDir)) return []
  const out: Project[] = []
  for (const ent of readdirSync(projectsDir, { withFileTypes: true })) {
    if (!ent.isDirectory()) continue
    const p = readProject(join(projectsDir, ent.name))
    if (p) out.push(p)
  }
  return out.sort((a, b) => a.name.localeCompare(b.name, 'zh'))
}

export function readProject(dir: string): Project | null {
  try {
    const raw = JSON.parse(readFileSync(join(dir, 'project.json'), 'utf8'))
    if (typeof raw.name !== 'string' || typeof raw.entry !== 'string') return null
    if (raw.start !== undefined) {
      if (typeof raw.start !== 'string' || typeof raw.port !== 'number') return null
    }
    return {
      dir,
      name: raw.name,
      icon: typeof raw.icon === 'string' ? raw.icon : 'apps',
      desc: typeof raw.desc === 'string' ? raw.desc : '',
      entry: raw.entry,
      start: raw.start,
      port: raw.port,
    }
  } catch { return null }
}
