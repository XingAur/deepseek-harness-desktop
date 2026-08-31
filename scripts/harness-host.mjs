import { existsSync, realpathSync } from 'node:fs'
import { isAbsolute, join } from 'node:path'
import { stdin, stdout } from 'node:process'
import { fileURLToPath } from 'node:url'

/** Core 内嵌 Python 的候选路径（按优先级）。打包布局是 runtime/，开发布局沿用 .venv/。 */
export function embeddedPythonCandidates(coreRoot, platform = process.platform) {
  if (!coreRoot) return []
  return platform === 'win32'
    ? [
        join(coreRoot, 'runtime', 'python.exe'),
        join(coreRoot, '.venv', 'Scripts', 'python.exe'),
      ]
    : [
        join(coreRoot, 'runtime', 'bin', 'python3'),
        join(coreRoot, 'runtime', 'bin', 'python'),
        join(coreRoot, '.venv', 'bin', 'python'),
      ]
}

async function main() {
  const hostModuleUrl = process.env.DSH_HARNESS_DEV_MODE === '1'
    ? new URL('../packages/dsh-plugin-desktop/lib/harness-host.js', import.meta.url)
    : new URL('./plugin/harness-host.js', import.meta.url)
  const { runDesktopHarnessHost } = await import(hostModuleUrl.href)

  const coreRoot = process.env.HARNESS_CORE_ROOT ?? ''
  const databasePath = process.env.HARNESS_DB_PATH ?? ''
  const devPython = process.env.DSH_HARNESS_DEV_MODE === '1' && process.env.HARNESS_PYTHON
    ? process.env.HARNESS_PYTHON
    : ''
  const bundled = embeddedPythonCandidates(coreRoot).find(existsSync) ?? ''
  const python = devPython || bundled

  if (!isAbsolute(coreRoot) || !isAbsolute(databasePath) || !isAbsolute(python)) {
    process.exitCode = 2
    return
  }
  // sidecar env 走传输层白名单；`python -m` 以 cwd=coreRoot 保证 app/tools 可导入。
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

function isMainModule() {
  if (!process.argv[1]) return false
  try {
    return realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)
  } catch {
    return false
  }
}

if (isMainModule()) {
  await main()
}
