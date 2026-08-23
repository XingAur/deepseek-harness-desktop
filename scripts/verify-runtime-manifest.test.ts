import { createHash, generateKeyPairSync, sign } from 'node:crypto'
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { describe, expect, it } from 'vitest'

import { canonicalJson } from './canonical-json.mjs'
import { verifyRuntimeManifest } from './verify-runtime-manifest.mjs'

describe('Runtime manifest verification', () => {
  it('accepts a valid signed archive and rejects a changed archive', () => {
    const directory = mkdtempSync(join(tmpdir(), 'dsh-runtime-verify-'))
    try {
      const archivePath = join(directory, 'dsh-runtime-darwin-aarch64.tar.gz')
      const manifestPath = join(directory, 'runtime-darwin-aarch64.json')
      const archive = Buffer.from('runtime bytes')
      writeFileSync(archivePath, archive)
      const { privateKey, publicKey } = generateKeyPairSync('ed25519')
      const publicJwk = publicKey.export({ format: 'jwk' })
      const manifest = {
        schemaVersion: 1,
        version: '0.1.9-preview',
        dshVersion: '0.1.0-rc.8',
        target: 'darwin-aarch64',
        url: 'https://github.com/example/repo/releases/download/runtime-v0.1.9-preview/dsh-runtime-darwin-aarch64.tar.gz',
        size: archive.length,
        sha256: createHash('sha256').update(archive).digest('hex'),
        archive: 'tar.gz',
        entrypoint: 'node',
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

      expect(verifyRuntimeManifest({
        manifestPath,
        archivePath,
        target: 'darwin-aarch64',
        version: '0.1.9-preview',
        publicKey: publicJwk.x!,
      })).toMatchObject({ target: 'darwin-aarch64', archive: 'tar.gz' })

      writeFileSync(archivePath, Buffer.from('changed runtime bytes'))
      expect(() => verifyRuntimeManifest({
        manifestPath,
        archivePath,
        target: 'darwin-aarch64',
        version: '0.1.9-preview',
        publicKey: publicJwk.x!,
      })).toThrow(/大小|SHA-256/)
    } finally {
      rmSync(directory, { recursive: true, force: true })
    }
  })
})
