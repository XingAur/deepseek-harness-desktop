import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { stdin, stdout } from 'node:process'

const hostModuleUrl = process.env.DSH_HARNESS_DEV_MODE === '1'
  ? new URL('../packages/dsh-plugin-desktop/lib/harness-host.js', import.meta.url)
  : new URL('./plugin/harness-host.js', import.meta.url)
const { runDesktopHarnessHost } = await import(hostModuleUrl.href)

const coreRoot = process.env.HARNESS_CORE_ROOT ?? ''
const databasePath = process.env.HARNESS_DB_PATH ?? ''
const bundledPython = coreRoot
  ? process.platform === 'win32'
    ? join(coreRoot, '.venv', 'Scripts', 'python.exe')
    : join(coreRoot, '.venv', 'bin', 'python')
  : ''
const python = process.env.DSH_HARNESS_DEV_MODE === '1' && process.env.HARNESS_PYTHON
  ? process.env.HARNESS_PYTHON
  : (bundledPython && existsSync(bundledPython) ? bundledPython : '')

if (!coreRoot.startsWith('/') || !databasePath.startsWith('/') || !python.startsWith('/')) {
  process.exitCode = 2
} else {
  await runDesktopHarnessHost({
    input: stdin,
    output: stdout,
    sidecar: {
      command: python,
      args: ['-u', '-m', 'tools.harness_host_server'],
      cwd: coreRoot,
      env: { HARNESS_DB_PATH: databasePath },
    },
  })
}
