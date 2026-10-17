#!/usr/bin/env node
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { createRequire } from 'node:module'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const moduleRoot = process.argv[2]
if (!moduleRoot) throw new Error('usage: test-repack-portable-asar.mjs <asar-module-root>')
const asar = createRequire(join(resolve(moduleRoot), 'package.json'))(resolve(moduleRoot))
const temporary = mkdtempSync(join(tmpdir(), 'dsh-portable-asar-'))
const archive = join(temporary, 'app.asar')
const source = join(temporary, 'source')
const extracted = join(temporary, 'extracted')
const nodeFile = 'dsh/node_modules/@img/sharp-darwin-arm64/lib/sharp-darwin-arm64-test.node'
const dylibFile = 'dsh/node_modules/@img/sharp-libvips-darwin-arm64/lib/libvips-cpp.8.18.7.dylib'
const ordinaryFile = 'dsh/desktop-runtime.json'
try {
  for (const [relative, content] of [[nodeFile, 'addon'], [dylibFile, 'library'], [ordinaryFile, 'original']]) {
    const full = join(source, relative)
    mkdirSync(dirname(full), { recursive: true })
    writeFileSync(full, content)
  }
  await asar.createPackageWithOptions(source, archive, { unpackDir: join('dsh', 'node_modules') })
  asar.extractAll(archive, extracted)
  writeFileSync(join(extracted, ordinaryFile), 'sanitized')
  execFileSync(process.execPath, [fileURLToPath(new URL('./repack-portable-asar.mjs', import.meta.url)),
    moduleRoot, archive, extracted, 'darwin-arm64'], { stdio: 'inherit' })
  asar.uncache(archive)
  for (const relative of [nodeFile, dylibFile]) {
    assert.equal(asar.statFile(archive, join(...relative.split('/'))).unpacked, true)
    assert.equal(readFileSync(join(`${archive}.unpacked`, relative), 'utf8'),
      relative === nodeFile ? 'addon' : 'library')
  }
  assert.equal(asar.extractFile(archive, join(...ordinaryFile.split('/'))).toString(), 'sanitized')
  console.log('portable ASAR native unpack regression passed')
} finally {
  rmSync(temporary, { recursive: true, force: true })
}
