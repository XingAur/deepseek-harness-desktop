import { spawnSync } from 'node:child_process'
import { copyFileSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { writeRuntimeLauncher } from './runtime-launcher.mjs'

const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')

describe('writeRuntimeLauncher', () => {
  it('rejects an invalid capability report before marker, plugin, or profile mutation', () => {
    const appDirectory = mkdtempSync(join(tmpdir(), 'dsh-runtime-launcher-app-'))
    const homeDirectory = mkdtempSync(join(tmpdir(), 'dsh-runtime-launcher-home-'))
    const mutationLog = join(appDirectory, 'mutations.log')
    try {
      writeRuntimeLauncher(appDirectory, { desktopPluginVersion: '0.3.2', desktopPluginSha256: 'sha256', runtimeVersion: '0.1.0' })
      copyFileSync(join(repositoryRoot, 'scripts', 'runtime-capabilities.mjs'), join(appDirectory, 'runtime-capabilities.mjs'))
      writeFileSync(join(appDirectory, 'runtime-capabilities.json'), JSON.stringify({ schemaVersion: 1 }))
      writeFileSync(join(appDirectory, 'desktop-profile.mjs'), "import { appendFileSync } from 'node:fs'; export function ensureDesktopProfile() { appendFileSync(process.env.MUTATION_LOG, 'profile\\n') }\n")
      writeFileSync(join(appDirectory, 'plugin-install-state.mjs'), "import { appendFileSync } from 'node:fs'; export async function markerMatches() { appendFileSync(process.env.MUTATION_LOG, 'marker-read\\n'); return true } export async function writeInstallMarker() { appendFileSync(process.env.MUTATION_LOG, 'marker-write\\n') }\n")
      writeFileSync(join(appDirectory, 'runtime-websocket-proxy.mjs'), 'export function attachRuntimeWebSocketProxy() {}\n')

      const result = spawnSync(process.execPath, [join(appDirectory, 'launcher.mjs'), '--port', '18888'], {
        env: { ...process.env, DSH_HOME: homeDirectory, MUTATION_LOG: mutationLog },
        encoding: 'utf8',
      })

      expect(result.status).not.toBe(0)
      expect(`${result.stdout}${result.stderr}`).toMatch(/capability/i)
      expect(() => readFileSync(mutationLog, 'utf8')).toThrow()
    } finally {
      rmSync(appDirectory, { recursive: true, force: true })
      rmSync(homeDirectory, { recursive: true, force: true })
    }
  })
})
