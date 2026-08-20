import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const productionUpdaterEndpoint = 'https://github.com/XingAur/DSH-Desktop/releases/latest/download/latest.json'

export function updaterConfig(publicKey, endpoint = productionUpdaterEndpoint) {
  if (typeof publicKey !== 'string' || publicKey.trim().length === 0) {
    throw new Error('TAURI_UPDATER_PUBLIC_KEY 不能为空')
  }
  if (endpoint !== productionUpdaterEndpoint) {
    throw new Error('应用更新地址必须使用固定的 GitHub latest.json endpoint')
  }
  return {
    bundle: { createUpdaterArtifacts: true },
    plugins: {
      updater: {
        pubkey: publicKey.trim(),
        endpoints: [productionUpdaterEndpoint],
        windows: { installMode: 'passive' },
      },
    },
  }
}

const entry = process.argv[1] ? resolve(process.argv[1]) : ''
if (entry === fileURLToPath(import.meta.url)) {
  try {
    const output = resolve(process.argv[2] ?? 'src-tauri/tauri.release.conf.json')
    const config = updaterConfig(process.env.TAURI_UPDATER_PUBLIC_KEY)
    writeFileSync(output, `${JSON.stringify(config, null, 2)}\n`, 'utf8')
    process.stdout.write(`${output}\n`)
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`)
    process.exitCode = 1
  }
}
