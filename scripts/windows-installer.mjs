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

export const MANAGED_RUNTIME_VERSION = '0.1.5-preview'
const MANAGED_RUNTIME_RELEASE_PATH = `/releases/download/runtime-v${MANAGED_RUNTIME_VERSION}/`

const portable = (value) => value.replaceAll('\\', '/')

export function createWindowsTauriConfig(rootDirectory) {
  const root = resolve(rootDirectory)
  return {
    bundle: {
      resources: {
        [portable(resolve(root, 'runtime-build/windows-x86_64/dsh-runtime-windows-x86_64.zip'))]:
          'runtime/dsh-runtime-windows-x86_64.zip',
        [portable(resolve(root, 'runtime-build/windows-x86_64/runtime-windows-x86_64.json'))]:
          'runtime/manifests/runtime-windows-x86_64.json',
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
  if (manifest.version !== MANAGED_RUNTIME_VERSION) {
    throw new Error(`Runtime version 必须是 ${MANAGED_RUNTIME_VERSION}`)
  }
  if (typeof manifest.url !== 'string' || !manifest.url.startsWith('https://')) {
    throw new Error('Runtime URL 必须使用 HTTPS')
  }
  if (!manifest.url.includes(MANAGED_RUNTIME_RELEASE_PATH)) {
    throw new Error(`Runtime URL 必须指向 runtime-v${MANAGED_RUNTIME_VERSION} Release`)
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

export function windowsInstallerName(version) {
  return `DeepSeek-Harness-v${version}-Windows-x64.exe`
}

export function tauriBuildInvocation(rootDirectory, generatedConfig, additionalConfigs = []) {
  const configArguments = [generatedConfig, ...additionalConfigs]
    .flatMap((config) => ['--config', portable(resolve(config))])
  return {
    command: process.execPath,
    args: [
      portable(resolve(rootDirectory, 'node_modules/@tauri-apps/cli/tauri.js')),
      'build',
      ...configArguments,
      '--bundles',
      'nsis',
    ],
  }
}

export function prepareWindowsInstallerConfig({
  rootDirectory = process.cwd(),
  environment = process.env,
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
  if (
    !manifestEndpoint?.startsWith('https://')
    || !manifestEndpoint.includes('{target}')
    || !manifestEndpoint.includes(MANAGED_RUNTIME_RELEASE_PATH)
  ) {
    throw new Error(
      `DSH_DESKTOP_RUNTIME_MANIFEST_URL 必须指向 runtime-v${MANAGED_RUNTIME_VERSION} 并包含 {target}`,
    )
  }

  const generatedDirectory = resolve(root, 'src-tauri/target/windows-installer')
  const generatedConfig = resolve(generatedDirectory, 'tauri.windows-installer.conf.json')
  mkdirSync(generatedDirectory, { recursive: true })
  writeFileSync(
    generatedConfig,
    `${JSON.stringify(createWindowsTauriConfig(root), null, 2)}\n`,
    'utf8',
  )
  return generatedConfig
}

export async function replaceReleaseInstaller({ generatedPath, releasePath }, build) {
  const generated = resolve(generatedPath)
  const release = resolve(releasePath)
  if (dirname(generated) !== dirname(release) || generated === release) {
    throw new Error('生成包和正式包必须是固定 NSIS 目录中的不同文件')
  }
  const previous = `${release}.previous`
  if (existsSync(previous)) throw new Error(`发现未恢复的正式包备份：${previous}`)
  rmSync(generated, { force: true })
  try {
    await build()
    if (!existsSync(generated)) throw new Error(`Tauri 未生成 NSIS 安装包：${generated}`)
    if (existsSync(release)) renameSync(release, previous)
    try {
      renameSync(generated, release)
      rmSync(previous, { force: true })
    } catch (error) {
      rmSync(release, { force: true })
      if (existsSync(previous)) renameSync(previous, release)
      throw error
    }
  } catch (error) {
    rmSync(generated, { force: true })
    throw error
  }
  return release
}

export async function buildWindowsInstaller({
  rootDirectory = process.cwd(),
  environment = process.env,
  run = spawnSync,
} = {}) {
  const root = resolve(rootDirectory)
  const generatedConfig = prepareWindowsInstallerConfig({ rootDirectory: root, environment })

  const appConfig = JSON.parse(readFileSync(resolve(root, 'src-tauri/tauri.conf.json'), 'utf8'))
  const outputDirectory = resolve(root, 'src-tauri/target/release/bundle/nsis')
  const generatedPath = resolve(
    outputDirectory,
    `${appConfig.productName}_${appConfig.version}_x64-setup.exe`,
  )
  const releasePath = resolve(outputDirectory, windowsInstallerName(appConfig.version))
  if (dirname(generatedPath) !== outputDirectory || dirname(releasePath) !== outputDirectory) {
    throw new Error('安装包输出路径越过固定 NSIS 目录')
  }
  const output = await replaceReleaseInstaller({ generatedPath, releasePath }, async () => {
    const { command, args } = tauriBuildInvocation(root, generatedConfig)
    const result = run(
      command,
      args,
      { cwd: root, env: environment, stdio: 'inherit' },
    )
    if (result.error) throw result.error
    if (result.status !== 0) {
      throw new Error(`Tauri Windows 安装包构建失败，退出码：${result.status ?? 'unknown'}`)
    }
  })

  const legacyPath = resolve(
    outputDirectory,
    `DeepSeek Harness Desktop_${appConfig.version}_x64-full-setup.exe`,
  )
  if (legacyPath !== releasePath) rmSync(legacyPath, { force: true })
  return output
}

const invokedPath = process.argv[1] ? resolve(process.argv[1]) : ''
if (invokedPath === resolve(fileURLToPath(import.meta.url))) {
  if (process.argv[2] === '--prepare-config') {
    const output = prepareWindowsInstallerConfig()
    console.log(`Windows installer config prepared: ${output}`)
  } else {
    const output = await buildWindowsInstaller()
    console.log(`Windows installer created: ${output}`)
  }
}
