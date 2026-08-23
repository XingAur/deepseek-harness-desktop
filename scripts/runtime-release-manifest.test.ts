import { createHash } from 'node:crypto'
import { mkdir, mkdtemp, readFile, symlink, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { createUnsignedRuntimeManifest, writeUnsignedRuntimeManifest } from './runtime-release-manifest.mjs'

describe('Runtime release manifest recovery', () => {
  it('deterministically recreates an unsigned manifest from an immutable archive', async () => {
    const root = await mkdtemp(join(tmpdir(), 'runtime-release-manifest-'))
    const archive = join(root, 'dsh-runtime-windows-x86_64.zip')
    const output = join(root, 'manifest-windows-x86_64.unsigned.json')
    await writeFile(archive, 'immutable-runtime')
    const url = 'https://github.com/XingAur/deepseek-harness-desktop/releases/download/runtime-v0.1.10-preview/dsh-runtime-windows-x86_64.zip'

    const result = writeUnsignedRuntimeManifest({
      archivePath: archive,
      outputPath: output,
      target: 'windows-x86_64',
      version: '0.1.10-preview',
      dshVersion: '0.1.1-rc.2',
      url,
    })

    expect(result.manifest).toEqual({
      schemaVersion: 1,
      version: '0.1.10-preview',
      dshVersion: '0.1.1-rc.2',
      target: 'windows-x86_64',
      url,
      size: 17,
      sha256: createHash('sha256').update('immutable-runtime').digest('hex'),
      archive: 'zip',
      entrypoint: 'node.exe',
      args: ['app/launcher.mjs', '--port', '{port}'],
      healthPath: '/__desktop/health',
      signature: '',
    })
    expect(JSON.parse(await readFile(output, 'utf8'))).toEqual(result.manifest)
  })

  it('rejects mismatched names, targets, URLs, versions, and symbolic links', async () => {
    const root = await mkdtemp(join(tmpdir(), 'runtime-release-manifest-invalid-'))
    const archive = join(root, 'dsh-runtime-darwin-aarch64.tar.gz')
    const links = join(root, 'links')
    const link = join(links, 'dsh-runtime-darwin-aarch64.tar.gz')
    await writeFile(archive, 'runtime')
    await mkdir(links)
    await symlink(archive, link)
    const base = {
      archivePath: archive,
      target: 'darwin-aarch64' as const,
      version: '0.1.10-preview',
      dshVersion: '0.1.1-rc.2',
      url: 'https://github.com/XingAur/deepseek-harness-desktop/releases/download/runtime-v0.1.10-preview/dsh-runtime-darwin-aarch64.tar.gz',
    }

    expect(() => createUnsignedRuntimeManifest({ ...base, target: 'linux-x86_64' as 'darwin-aarch64' })).toThrow(/target/)
    expect(() => createUnsignedRuntimeManifest({ ...base, version: 'latest' })).toThrow(/SemVer/)
    expect(() => createUnsignedRuntimeManifest({ ...base, url: `${base.url}?token=secret` })).toThrow(/URL/)
    expect(() => createUnsignedRuntimeManifest({ ...base, url: base.url.replace('.tar.gz', '.zip') })).toThrow(/指向/)
    expect(() => createUnsignedRuntimeManifest({ ...base, archivePath: link })).toThrow(/普通文件/)
  })
})
