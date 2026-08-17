import { spawnSync } from 'node:child_process'
import { existsSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const REGISTRIES = ['https://registry.npmmirror.com', 'https://registry.npmjs.org']

export interface LatestInfo { version: string; tarball: string }

export async function fetchLatest(): Promise<LatestInfo> {
  for (const reg of REGISTRIES) {
    try {
      const res = await fetch(`${reg}/@deepseek-ai/dsh/latest`)
      if (!res.ok) continue
      const j = await res.json() as { version: string; dist: { tarball: string } }
      return { version: j.version, tarball: j.dist.tarball }
    } catch { continue }
  }
  throw new Error('REGISTRY_UNREACHABLE')
}

export interface InstallOpts { runPnpm?: (args: string[], cwd: string) => { status: number | null } }

export async function installUserVersion(runtimeDshDir: string, version: string, opts: InstallOpts = {}): Promise<string> {
  const dest = join(runtimeDshDir, version)
  if (existsSync(join(dest, 'node_modules', '@deepseek-ai', 'dsh'))) return dest
  mkdirSync(dest, { recursive: true })
  writeFileSync(join(dest, 'package.json'), JSON.stringify({ name: 'dsh-user', private: true }, null, 2))
  const runPnpm = opts.runPnpm ?? ((args: string[], cwd: string) => {
    const r = spawnSync('cmd.exe', ['/c', 'pnpm.cmd', ...args], { cwd, stdio: 'pipe' })
    return { status: r.status }
  })
  const result = runPnpm(['add', `@deepseek-ai/dsh@${version}`], dest)
  if (result.status !== 0) {
    rmSync(dest, { recursive: true, force: true })
    throw new Error('INSTALL_FAILED')
  }
  return dest
}
