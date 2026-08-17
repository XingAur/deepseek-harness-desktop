import { mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

export interface Settings { dataRoot: string; dshVersion?: string }

export function resolveDataRoot(override?: string, diskProbe: () => boolean = () => true): string {
  const root = override ?? (diskProbe() ? 'D:\\DeepSeekHarness' : 'C:\\DeepSeekHarness')
  mkdirSync(root, { recursive: true })
  return root
}

export interface AppPaths {
  dataRoot: string
  projectsDir: string
  dshHome: string
  runtimeDshDir: string
  binDir: string
  logsDir: string
  settingsFile: string
}

export function appPaths(dataRoot: string): AppPaths {
  return {
    dataRoot,
    projectsDir: join(dataRoot, 'projects'),
    dshHome: join(dataRoot, 'dsh-home'),
    runtimeDshDir: join(dataRoot, 'runtime', 'dsh'),
    binDir: join(dataRoot, 'bin'),
    logsDir: join(dataRoot, 'logs'),
    settingsFile: join(dataRoot, 'settings.json'),
  }
}

export function loadSettings(file: string): Settings {
  try { return JSON.parse(readFileSync(file, 'utf8')) } catch { return { dataRoot: '' } }
}

export function saveSettings(file: string, s: Settings): void {
  writeFileSync(file, JSON.stringify(s, null, 2), 'utf8')
}

export function dirWritable(dir: string): boolean {
  try {
    mkdirSync(dir, { recursive: true })
    const probe = join(dir, '.write-probe')
    writeFileSync(probe, '1')
    rmSync(probe)
    return true
  } catch { return false }
}
