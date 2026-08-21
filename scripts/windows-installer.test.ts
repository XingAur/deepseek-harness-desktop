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
  createWindowsTauriConfig,
  replaceReleaseInstaller,
  tauriBuildInvocation,
  verifyBundledRuntime,
  windowsInstallerName,
} from './windows-installer.mjs'

describe('Windows installer contract', () => {
  it('embeds the signed Runtime without installer-time hooks', () => {
    const config = createWindowsTauriConfig('E:/repo')
    expect(config.bundle.resources).toEqual({
      'E:/repo/runtime-build/windows-x86_64/dsh-runtime-windows-x86_64.zip':
        'runtime/dsh-runtime-windows-x86_64.zip',
      'E:/repo/runtime-build/windows-x86_64/runtime-windows-x86_64.json':
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
        version: '0.1.2-preview',
        dshVersion: '0.1.0-rc.8',
        target: 'windows-x86_64',
        url: 'https://github.com/example/runtime.zip',
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
        version: '0.1.2-preview',
      })
      expect(() => verifyBundledRuntime({
        manifestPath,
        archivePath: corruptArchivePath,
        publicKey: publicJwk.x!,
      })).toThrow(/大小|SHA-256/)
    } finally {
      rmSync(directory, { recursive: true, force: true })
    }
  })

  it('uses the deterministic Release installer name', () => {
    expect(windowsInstallerName('0.1.0')).toBe(
      'DeepSeek-Harness-v0.1.0-Windows-x64.exe',
    )
  })

  it('launches the pinned Tauri CLI through Node instead of a Windows cmd shim', () => {
    const invocation = tauriBuildInvocation('E:/repo', 'E:/repo/generated.json')
    expect(invocation.command).toBe(process.execPath)
    expect(invocation.args).toEqual([
      'E:/repo/node_modules/@tauri-apps/cli/tauri.js',
      'build',
      '--config',
      'E:/repo/generated.json',
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
