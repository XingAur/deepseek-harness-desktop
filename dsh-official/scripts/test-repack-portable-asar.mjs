#!/usr/bin/env node
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { chmodSync, lstatSync, mkdtempSync, mkdirSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { createRequire } from 'node:module'
import { basename, dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const moduleRoot = process.argv[2]
if (!moduleRoot) throw new Error('usage: test-repack-portable-asar.mjs <asar-module-root>')
// Darwin fixture only. Running this on Windows does not verify PE INTEGRITY/ELECTRONASAR updates.
const asar = createRequire(join(resolve(moduleRoot), 'package.json'))(resolve(moduleRoot))
const temporaryRoot = realpathSync(tmpdir())
const temporary = realpathSync(mkdtempSync(join(temporaryRoot, 'dsh-portable-asar-')))
const resources = join(temporary, 'Fixture.app', 'Contents', 'Resources')
mkdirSync(resources, { recursive: true })
const archive = join(resources, 'app.asar')
const info = join(resources, '..', 'Info.plist')
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
    if (relative === nodeFile || relative === dylibFile) chmodSync(full, 0o755)
  }
  if (process.platform === 'darwin') {
    writeFileSync(info, '<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict><key>ElectronAsarIntegrity</key><dict><key>Resources/app.asar</key><dict><key>algorithm</key><string>SHA256</string><key>hash</key><string>old-header</string></dict></dict></dict></plist>')
  }
  await asar.createPackageWithOptions(source, archive, { unpackDir: join('dsh', 'node_modules') })
  asar.extractAll(archive, extracted)
  // Model extractAll's missing executable-bit preservation explicitly.
  chmodSync(join(extracted, nodeFile), 0o644)
  chmodSync(join(extracted, dylibFile), 0o644)
  writeFileSync(join(extracted, ordinaryFile), 'sanitized')
  execFileSync(process.execPath, [fileURLToPath(new URL('./repack-portable-asar.mjs', import.meta.url)),
    moduleRoot, archive, extracted, 'darwin-arm64'], { stdio: 'inherit' })
  asar.uncache(archive)
  for (const relative of [nodeFile, dylibFile]) {
    assert.equal(asar.statFile(archive, join(...relative.split('/'))).unpacked, true)
    assert.equal(readFileSync(join(`${archive}.unpacked`, relative), 'utf8'),
      relative === nodeFile ? 'addon' : 'library')
    if (process.platform !== 'win32') assert.equal(lstatSync(join(`${archive}.unpacked`, relative)).mode & 0o111, 0o111)
  }
  assert.equal(asar.extractFile(archive, join(...ordinaryFile.split('/'))).toString(), 'sanitized')
  if (process.platform === 'darwin') {
    const expected = createHash('sha256').update(asar.getRawHeader(archive).headerString).digest('hex')
    const actual = execFileSync('/usr/libexec/PlistBuddy', ['-c', 'Print :ElectronAsarIntegrity:Resources/app.asar:hash', info], { encoding: 'utf8' }).trim()
    assert.equal(actual, expected, 'ASAR header digest must match the repacked archive')
  }
  console.log('portable ASAR native unpack and integrity regression passed')
} finally {
  assert.equal(dirname(realpathSync(temporary)), temporaryRoot)
  assert.ok(basename(temporary).startsWith('dsh-portable-asar-'))
  rmSync(temporary, { recursive: true, force: true })
}
