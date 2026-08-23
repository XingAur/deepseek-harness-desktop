import { createHash, generateKeyPairSync, sign } from 'node:crypto'
import {
  existsSync,
  mkdtempSync,
  mkdirSync,
  readFileSync,
  rmSync,
  writeFileSync,
} from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { describe, expect, it } from 'vitest'

import { canonicalJson } from './canonical-json.mjs'
import {
  MANAGED_RUNTIME_VERSION,
  createWindowsTauriConfig,
  prepareWindowsInstallerConfig,
  replaceReleaseInstaller,
  tauriBuildInvocation,
  verifyBundledRuntime,
  windowsInstallerName,
} from './windows-installer.mjs'

describe('Windows installer contract', () => {
  it('embeds the signed Runtime without installer-time hooks', () => {
    // posix 上没有盘符路径：用平台各自的绝对根，避免 resolve 把 'E:/repo' 当相对路径。
    const root = process.platform === 'win32' ? 'E:/repo' : '/opt/dsh-repo'
    const config = createWindowsTauriConfig(root)
    expect(config.bundle.resources).toEqual({
      [`${root}/runtime-build/windows-x86_64/dsh-runtime-windows-x86_64.zip`]:
        'runtime/dsh-runtime-windows-x86_64.zip',
      [`${root}/runtime-build/windows-x86_64/runtime-windows-x86_64.json`]:
        'runtime/manifests/runtime-windows-x86_64.json',
    })
    expect(config.bundle).not.toHaveProperty('windows')
    expect(existsSync('src-tauri/windows/full-installer-hooks.nsh')).toBe(false)
  })

  it('verifies the signed Windows Runtime and rejects a changed archive', () => {
    const directory = mkdtempSync(join(tmpdir(), 'dsh-windows-installer-'))
    try {
      const archivePath = join(directory, 'dsh-runtime-windows-x86_64.zip')
      const corruptDirectory = join(directory, 'corrupt')
      const corruptArchivePath = join(corruptDirectory, 'dsh-runtime-windows-x86_64.zip')
      const manifestPath = join(directory, 'runtime-windows-x86_64.json')
      const archive = Buffer.from('signed runtime bytes')
      mkdirSync(corruptDirectory)
      writeFileSync(archivePath, archive)
      writeFileSync(corruptArchivePath, Buffer.from('changed runtime bytes'))
      const { privateKey, publicKey } = generateKeyPairSync('ed25519')
      const publicJwk = publicKey.export({ format: 'jwk' })
      const manifest = {
        schemaVersion: 1,
        version: '0.1.7-preview',
        dshVersion: '0.1.0-rc.8',
        target: 'windows-x86_64',
        url: 'https://github.com/example/repo/releases/download/runtime-v0.1.7-preview/dsh-runtime-windows-x86_64.zip',
        size: archive.length,
        sha256: createHash('sha256').update(archive).digest('hex'),
        archive: 'zip',
        entrypoint: 'node.exe',
        args: [],
        healthPath: '/__desktop/health',
        signature: '',
      }
      manifest.signature = sign(
        null,
        Buffer.from(canonicalJson(manifest, 'signature')),
        privateKey,
      ).toString('base64url')
      writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`)

      expect(verifyBundledRuntime({
        manifestPath,
        archivePath,
        publicKey: publicJwk.x!,
      })).toMatchObject({
        target: 'windows-x86_64',
        archive: 'zip',
        version: '0.1.7-preview',
      })
      const wrongRelease = {
        ...manifest,
        url: 'https://github.com/example/repo/releases/download/runtime-v0.1.6-preview/dsh-runtime-windows-x86_64.zip',
      }
      writeFileSync(manifestPath, `${JSON.stringify(wrongRelease, null, 2)}\n`)
      expect(() => verifyBundledRuntime({
        manifestPath,
        archivePath,
        publicKey: publicJwk.x!,
      })).toThrow(/runtime-v0.1.7-preview/)
      writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`)
      expect(() => verifyBundledRuntime({
        manifestPath,
        archivePath: corruptArchivePath,
        publicKey: publicJwk.x!,
      })).toThrow(/大小|SHA-256/)
    } finally {
      rmSync(directory, { recursive: true, force: true })
    }
  })

  it('pins the managed Runtime release version', () => {
    expect(MANAGED_RUNTIME_VERSION).toBe('0.1.7-preview')
  })

  it('verifies the Runtime and writes the reusable Windows Tauri config', () => {
    const root = mkdtempSync(join(tmpdir(), 'dsh-windows-config-'))
    try {
      const runtimeDirectory = join(root, 'runtime-build', 'windows-x86_64')
      mkdirSync(runtimeDirectory, { recursive: true })
      const archivePath = join(runtimeDirectory, 'dsh-runtime-windows-x86_64.zip')
      const manifestPath = join(runtimeDirectory, 'runtime-windows-x86_64.json')
      const archive = Buffer.from('prepared runtime bytes')
      writeFileSync(archivePath, archive)
      const { privateKey, publicKey } = generateKeyPairSync('ed25519')
      const publicJwk = publicKey.export({ format: 'jwk' })
      const manifest = {
        schemaVersion: 1,
        version: MANAGED_RUNTIME_VERSION,
        dshVersion: '0.1.0-rc.8',
        target: 'windows-x86_64',
        url: 'https://github.com/example/repo/releases/download/runtime-v0.1.7-preview/dsh-runtime-windows-x86_64.zip',
        size: archive.length,
        sha256: createHash('sha256').update(archive).digest('hex'),
        archive: 'zip',
        entrypoint: 'node.exe',
        args: [],
        healthPath: '/__desktop/health',
        signature: '',
      }
      manifest.signature = sign(
        null,
        Buffer.from(canonicalJson(manifest, 'signature')),
        privateKey,
      ).toString('base64url')
      writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`)

      const generatedConfig = prepareWindowsInstallerConfig({
        rootDirectory: root,
        environment: {
          DSH_DESKTOP_RELEASE_PUBLIC_KEY: publicJwk.x!,
          DSH_DESKTOP_RUNTIME_MANIFEST_URL:
            'https://github.com/example/repo/releases/download/runtime-v0.1.7-preview/runtime-{target}.json',
        },
      })

      expect(generatedConfig.replaceAll('\\', '/')).toBe(
        `${root.replaceAll('\\', '/')}/src-tauri/target/windows-installer/tauri.windows-installer.conf.json`,
      )
      expect(JSON.parse(readFileSync(generatedConfig, 'utf8'))).toEqual(
        createWindowsTauriConfig(root),
      )
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  it('uses the deterministic Release installer name', () => {
    expect(windowsInstallerName('0.1.0')).toBe(
      'DeepSeek-Harness-v0.1.0-Windows-x64.exe',
    )
  })

  it('launches the pinned Tauri CLI through Node instead of a Windows cmd shim', () => {
    const root = process.platform === 'win32' ? 'E:/repo' : '/opt/dsh-repo'
    const invocation = tauriBuildInvocation(
      root,
      `${root}/generated.json`,
      [`${root}/src-tauri/tauri.release.conf.json`],
    )
    expect(invocation.command).toBe(process.execPath)
    expect(invocation.args).toEqual([
      `${root}/node_modules/@tauri-apps/cli/tauri.js`,
      'build',
      '--config',
      `${root}/generated.json`,
      '--config',
      `${root}/src-tauri/tauri.release.conf.json`,
      '--bundles',
      'nsis',
    ])
  })

  it('promotes the generated installer as the only Release artifact', async () => {
    const directory = mkdtempSync(join(tmpdir(), 'dsh-release-artifact-'))
    try {
      const generatedPath = join(directory, 'generated.exe')
      const releasePath = join(directory, 'DeepSeek-Harness-v0.1.0-Windows-x64.exe')

      await replaceReleaseInstaller({ generatedPath, releasePath }, async () => {
        writeFileSync(generatedPath, 'new-release')
      })

      expect(readFileSync(releasePath, 'utf8')).toBe('new-release')
      expect(existsSync(generatedPath)).toBe(false)
      expect(existsSync(`${releasePath}.previous`)).toBe(false)
    } finally {
      rmSync(directory, { recursive: true, force: true })
    }
  })

  it('keeps the previous Release installer and removes a partial default build on failure', async () => {
    const directory = mkdtempSync(join(tmpdir(), 'dsh-release-failure-'))
    try {
      const generatedPath = join(directory, 'generated.exe')
      const releasePath = join(directory, 'DeepSeek-Harness-v0.1.0-Windows-x64.exe')
      writeFileSync(releasePath, 'previous-release')

      await expect(replaceReleaseInstaller({ generatedPath, releasePath }, async () => {
        writeFileSync(generatedPath, 'partial-build')
        throw new Error('simulated build failure')
      })).rejects.toThrow(/simulated build failure/)

      expect(readFileSync(releasePath, 'utf8')).toBe('previous-release')
      expect(existsSync(generatedPath)).toBe(false)
      expect(existsSync(`${releasePath}.previous`)).toBe(false)
    } finally {
      rmSync(directory, { recursive: true, force: true })
    }
  })

  it('rejects paths outside one fixed NSIS output directory', async () => {
    const directory = mkdtempSync(join(tmpdir(), 'dsh-release-paths-'))
    try {
      const generatedPath = join(directory, 'generated.exe')
      await expect(replaceReleaseInstaller({
        generatedPath,
        releasePath: join(directory, 'nested', 'release.exe'),
      }, async () => {})).rejects.toThrow(/固定 NSIS 目录/)
      await expect(replaceReleaseInstaller({
        generatedPath,
        releasePath: generatedPath,
      }, async () => {})).rejects.toThrow(/不同文件/)
    } finally {
      rmSync(directory, { recursive: true, force: true })
    }
  })
})
