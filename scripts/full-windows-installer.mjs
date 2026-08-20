import { createHash, createPublicKey, verify } from 'node:crypto'
import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { basename, dirname, resolve } from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

import { canonicalJson } from './canonical-json.mjs'

const portable = (value) => value.replaceAll('\\', '/')

export function createFullTauriConfig(rootDirectory) {
  const root = resolve(rootDirectory)
  return {
    bundle: {
      resources: {
        [portable(resolve(root, 'runtime-build/windows-x86_64/dsh-runtime-windows-x86_64.zip'))]:
          'runtime/dsh-runtime-windows-x86_64.zip',
        [portable(resolve(root, 'runtime-build/windows-x86_64/runtime-windows-x86_64.json'))]:
          'runtime/manifests/runtime-windows-x86_64.json',
      },
      windows: {
        nsis: {
          installerHooks: portable(resolve(root, 'src-tauri/windows/full-installer-hooks.nsh')),
        },
      },
    },
  }
}

export function verifyBundledRuntime({ manifestPath, archivePath, publicKey }) {
  if (!publicKey) throw new Error('DSH_DESKTOP_RELEASE_PUBLIC_KEY is required')
  if (basename(manifestPath) !== 'runtime-windows-x86_64.json') {
    throw new Error('Runtime 清单文件名必须是 runtime-windows-x86_64.json')
  }
  if (basename(archivePath) !== 'dsh-runtime-windows-x86_64.zip') {
    throw new Error('Runtime ZIP 文件名必须是 dsh-runtime-windows-x86_64.zip')
  }
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
  if (manifest.target !== 'windows-x86_64') {
    throw new Error('Runtime target 必须是 windows-x86_64')
  }
  if (manifest.archive !== 'zip') {
    throw new Error('Windows Runtime archive 必须是 zip')
  }
  if (manifest.version !== '0.1.0-preview') {
    throw new Error('Runtime version 必须是 0.1.0-preview')
  }
  if (typeof manifest.url !== 'string' || !manifest.url.startsWith('https://')) {
    throw new Error('Runtime URL 必须使用 HTTPS')
  }
  const key = createPublicKey({
    key: { kty: 'OKP', crv: 'Ed25519', x: publicKey },
    format: 'jwk',
  })
  const signature = Buffer.from(manifest.signature, 'base64url')
  if (!verify(null, Buffer.from(canonicalJson(manifest, 'signature')), key, signature)) {
    throw new Error('Runtime 清单签名校验失败')
  }
  const archive = readFileSync(archivePath)
  if (archive.length !== manifest.size) {
    throw new Error(`Runtime 归档大小不匹配：${archive.length}/${manifest.size}`)
  }
  const sha256 = createHash('sha256').update(archive).digest('hex')
  if (sha256.toLowerCase() !== String(manifest.sha256).toLowerCase()) {
    throw new Error('Runtime 归档 SHA-256 不匹配')
  }
  return manifest
}

export function fullInstallerName(version) {
  return `DeepSeek Harness Desktop_${version}_x64-full-setup.exe`
}

export async function withPreservedOnlineInstaller({ onlinePath, fullPath }, build) {
  const online = resolve(onlinePath)
  const full = resolve(fullPath)
  if (dirname(online) !== dirname(full)) {
    throw new Error('线上包和完全包必须位于同一个 NSIS 输出目录')
  }
  const backup = `${online}.preserved-online`
  if (existsSync(backup)) throw new Error(`发现未恢复的线上包备份：${backup}`)
  const hadOnline = existsSync(online)
  if (hadOnline) renameSync(online, backup)
  try {
    await build()
    if (!existsSync(online)) throw new Error(`Tauri 未生成 NSIS 安装包：${online}`)
    rmSync(full, { force: true })
    renameSync(online, full)
  } finally {
    if (existsSync(backup)) {
      rmSync(online, { force: true })
      renameSync(backup, online)
    }
  }
  return full
}

export function buildFullWindowsInstaller({
  rootDirectory = process.cwd(),
  environment = process.env,
  run = spawnSync,
} = {}) {
  const root = resolve(rootDirectory)
  const manifestPath = resolve(
    root,
    'runtime-build/windows-x86_64/runtime-windows-x86_64.json',
  )
  const archivePath = resolve(
    root,
    'runtime-build/windows-x86_64/dsh-runtime-windows-x86_64.zip',
  )
  verifyBundledRuntime({
    manifestPath,
    archivePath,
    publicKey: environment.DSH_DESKTOP_RELEASE_PUBLIC_KEY,
  })
  const manifestEndpoint = environment.DSH_DESKTOP_RUNTIME_MANIFEST_URL
  if (!manifestEndpoint?.startsWith('https://') || !manifestEndpoint.includes('{target}')) {
    throw new Error('DSH_DESKTOP_RUNTIME_MANIFEST_URL 必须是包含 {target} 的 HTTPS 地址')
  }

  const appConfig = JSON.parse(readFileSync(resolve(root, 'src-tauri/tauri.conf.json'), 'utf8'))
  const outputDirectory = resolve(root, 'src-tauri/target/release/bundle/nsis')
  const onlinePath = resolve(
    outputDirectory,
    `${appConfig.productName}_${appConfig.version}_x64-setup.exe`,
  )
  const fullPath = resolve(outputDirectory, fullInstallerName(appConfig.version))
  if (dirname(onlinePath) !== outputDirectory || dirname(fullPath) !== outputDirectory) {
    throw new Error('安装包输出路径越过固定 NSIS 目录')
  }
  const generatedDirectory = resolve(root, 'src-tauri/target/full-installer')
  const generatedConfig = resolve(generatedDirectory, 'tauri.full.conf.json')
  mkdirSync(generatedDirectory, { recursive: true })
  writeFileSync(
    generatedConfig,
    `${JSON.stringify(createFullTauriConfig(root), null, 2)}\n`,
    'utf8',
  )

  return withPreservedOnlineInstaller({ onlinePath, fullPath }, async () => {
    const command = process.platform === 'win32' ? 'npx.cmd' : 'npx'
    const result = run(
      command,
      ['tauri', 'build', '--config', generatedConfig, '--bundles', 'nsis'],
      { cwd: root, env: environment, stdio: 'inherit' },
    )
    if (result.error) throw result.error
    if (result.status !== 0) {
      throw new Error(`Tauri 完全安装包构建失败，退出码：${result.status ?? 'unknown'}`)
    }
  })
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : ''
if (invokedPath === resolve(fileURLToPath(import.meta.url))) {
  const output = await buildFullWindowsInstaller()
  console.log(`Full Windows installer created: ${output}`)
}
