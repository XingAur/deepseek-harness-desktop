import { writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export const productionUpdaterEndpoint = 'https://github.com/XingAur/deepseek-harness-desktop/releases/latest/download/latest.json'

export function updaterConfig({ platform, publicKey, endpoint = productionUpdaterEndpoint }) {
  const bundle = {
    createUpdaterArtifacts: platform === 'windows-x86_64',
    resources: { '../runtime/': 'runtime/' },
  }
  if (platform === 'darwin-aarch64') return { bundle }
  if (platform !== 'windows-x86_64') throw new Error(`不支持的 updater platform: ${String(platform)}`)
  if (typeof publicKey !== 'string' || publicKey.trim().length === 0) {
    throw new Error('Windows updater public key 不能为空')
  }
  if (endpoint !== productionUpdaterEndpoint) {
    throw new Error('应用更新地址必须使用固定的 GitHub latest.json endpoint')
  }
  return {
    bundle,
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
    const platform = process.argv.slice(2).find((value) => value.startsWith('--platform='))?.slice('--platform='.length)
    const output = resolve(
      process.argv.slice(2).find((value) => value.startsWith('--output='))?.slice('--output='.length)
        ?? 'src-tauri/tauri.release.conf.json',
    )
    const config = updaterConfig({
      platform,
      publicKey: process.env.TAURI_UPDATER_PUBLIC_KEY,
    })
    writeFileSync(output, `${JSON.stringify(config, null, 2)}\n`, 'utf8')
    process.stdout.write(`${output}\n`)
  } catch (error) {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`)
    process.exitCode = 1
  }
}
