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
  createFullTauriConfig,
  fullInstallerName,
  tauriBuildInvocation,
  verifyBundledRuntime,
  withPreservedOnlineInstaller,
} from './full-windows-installer.mjs'

describe('full Windows installer contract', () => {
  it('keeps the online installer free of Runtime resources and install hooks', () => {
    const online = JSON.parse(readFileSync('src-tauri/tauri.windows.conf.json', 'utf8'))
    expect(online.bundle.resources).toBeUndefined()
    expect(online.bundle.windows.nsis.installerHooks).toBeUndefined()
  })

  it('keeps the full installer copy-only while embedding Runtime resources', () => {
    const config = createFullTauriConfig('E:/repo')
    expect(config.bundle.resources).toEqual({
      'E:/repo/runtime-build/windows-x86_64/dsh-runtime-windows-x86_64.zip':
        'runtime/dsh-runtime-windows-x86_64.zip',
      'E:/repo/runtime-build/windows-x86_64/runtime-windows-x86_64.json':
        'runtime/manifests/runtime-windows-x86_64.json',
    })
    expect(config.bundle.windows?.nsis?.installerHooks).toBeUndefined()
    expect(existsSync('src-tauri/windows/full-installer-hooks.nsh')).toBe(false)
  })

  it('verifies the signed Windows Runtime and rejects a changed archive', () => {
    const directory = mkdtempSync(join(tmpdir(), 'dsh-full-installer-'))
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
        version: '0.1.0-preview',
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
        version: '0.1.0-preview',
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

  it('uses the deterministic full installer name', () => {
    expect(fullInstallerName('0.1.0')).toBe(
      'DeepSeek Harness Desktop_0.1.0_x64-full-setup.exe',
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

  it('restores an existing online installer after moving the full build', async () => {
    const directory = mkdtempSync(join(tmpdir(), 'dsh-full-artifacts-'))
    try {
      const outputDirectory = join(directory, 'src-tauri/target/release/bundle/nsis')
      mkdirSync(outputDirectory, { recursive: true })
      const onlinePath = join(outputDirectory, 'online.exe')
      const fullPath = join(outputDirectory, 'full.exe')
      writeFileSync(onlinePath, 'existing-online-build')

      await withPreservedOnlineInstaller({ onlinePath, fullPath }, async () => {
        expect(existsSync(onlinePath)).toBe(false)
        writeFileSync(onlinePath, 'new-full-build')
      })

      expect(readFileSync(onlinePath, 'utf8')).toBe('existing-online-build')
      expect(readFileSync(fullPath, 'utf8')).toBe('new-full-build')
    } finally {
      rmSync(directory, { recursive: true, force: true })
    }
  })

  it('restores the online installer when the full build fails', async () => {
    const directory = mkdtempSync(join(tmpdir(), 'dsh-full-failure-'))
    try {
      const onlinePath = join(directory, 'online.exe')
      const fullPath = join(directory, 'full.exe')
      writeFileSync(onlinePath, 'existing-online-build')

      await expect(withPreservedOnlineInstaller({ onlinePath, fullPath }, async () => {
        writeFileSync(onlinePath, 'partial-full-build')
        throw new Error('simulated build failure')
      })).rejects.toThrow(/simulated build failure/)

      expect(readFileSync(onlinePath, 'utf8')).toBe('existing-online-build')
      expect(existsSync(fullPath)).toBe(false)
    } finally {
      rmSync(directory, { recursive: true, force: true })
    }
  })
})
