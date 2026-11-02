#!/usr/bin/env node
// Preserve electron-builder's native unpacking while sanitizing a disposable ASAR.
import { chmodSync, existsSync, lstatSync, renameSync, rmSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { basename, dirname, join, resolve, sep } from 'node:path'
import { asarArchiveHeaderSha256, updateWinAsarIntegrity } from './windows-portable-integrity.mjs'

const [moduleRoot, archive, extracted, platform] = process.argv.slice(2)
if (!moduleRoot || !archive || !extracted || !['darwin-arm64', 'win32-x64'].includes(platform)) {
  throw new Error('usage: repack-portable-asar.mjs <asar-module-root> <app.asar> <extracted-tree> <darwin-arm64|win32-x64>')
}
const require = createRequire(join(resolve(moduleRoot), 'package.json'))
const asar = require(resolve(moduleRoot))
const original = resolve(archive)
const output = `${original}.sanitized`
const originalUnpacked = `${original}.unpacked`
const outputUnpacked = `${output}.unpacked`

function unpackedFiles(node, prefix = '') {
  if (!node.files) return node.unpacked ? [prefix] : []
  return Object.entries(node.files).flatMap(([name, child]) =>
    unpackedFiles(child, prefix ? `${prefix}/${name}` : name))
}

const previous = unpackedFiles(asar.getRawHeader(original).header)
if (previous.length === 0) throw new Error('electron-builder ASAR contained no unpacked native files')
if (previous.some(file => !file.startsWith('dsh/node_modules/'))) {
  throw new Error('electron-builder unpacked a path outside dsh/node_modules; update the portable repack rule')
}
if (platform === 'darwin-arm64') {
  for (const pattern of [
    /^dsh\/node_modules\/@img\/sharp-darwin-arm64\/lib\/[^/]+\.node$/,
    /^dsh\/node_modules\/@img\/sharp-libvips-darwin-arm64\/lib\/libvips-cpp[^/]+\.dylib$/,
  ]) {
    if (!previous.some(file => pattern.test(file))) throw new Error(`missing original sharp native dependency: ${pattern}`)
  }
}

// ASAR headers do not retain executable bits for already-unpacked files.
// extractAll creates those files with default permissions; restore the trusted
// physical input's execute bits before creating the replacement archive.
const extractedRoot = resolve(extracted)
for (const file of previous) {
  const source = resolve(originalUnpacked, file)
  const target = resolve(extractedRoot, file)
  if (!source.startsWith(`${originalUnpacked}${sep}`) || !target.startsWith(`${extractedRoot}${sep}`)
    || !lstatSync(source).isFile() || !lstatSync(target).isFile()) {
    throw new Error('ASAR unpacked executable source must be an ordinary in-tree file')
  }
  if (platform === 'win32-x64') continue
  const targetMode = lstatSync(target).mode
  const sourceMode = lstatSync(source).mode
  chmodSync(target, (targetMode & 0o666) | (sourceMode & 0o111))
}
rmSync(output, { force: true })
rmSync(outputUnpacked, { recursive: true, force: true })
await asar.createPackageWithOptions(resolve(extracted), output, { unpackDir: join('dsh', 'node_modules') })
for (const file of previous) {
  if (asar.statFile(output, join(...file.split('/'))).unpacked !== true || !existsSync(join(outputUnpacked, file))) {
    throw new Error(`ASAR repack lost an unpacked native dependency: ${file}`)
  }
}
const headerDigest = createHash('sha256').update(asar.getRawHeader(output).headerString).digest('hex')
rmSync(original, { force: true })
rmSync(originalUnpacked, { recursive: true, force: true })
renameSync(output, original)
renameSync(outputUnpacked, originalUnpacked)
if (platform === 'darwin-arm64' && basename(original) === 'app.asar'
  && basename(dirname(original)) === 'Resources' && basename(dirname(dirname(original))) === 'Contents') {
  const info = join(dirname(original), '..', 'Info.plist')
  if (existsSync(info)) {
    execFileSync('/usr/libexec/PlistBuddy', ['-c', `Set :ElectronAsarIntegrity:Resources/app.asar:hash ${headerDigest}`, info])
  }
}
if (platform === 'win32-x64' && basename(original) === 'app.asar' && basename(dirname(original)) === 'resources') {
  const executable = join(dirname(original), '..', 'DeepSeek Harness.exe')
  if (!existsSync(executable) || !lstatSync(executable).isFile() || lstatSync(executable).isSymbolicLink()) {
    throw new Error('Windows unpacked app is missing DeepSeek Harness.exe')
  }
  const requireFromAsar = createRequire(join(resolve(moduleRoot), 'package.json'))
  const reseditRoot = join(dirname(resolve(moduleRoot)), 'resedit')
  const resedit = existsSync(join(reseditRoot, 'package.json')) ? requireFromAsar(reseditRoot) : requireFromAsar('resedit')
  asar.uncache(original)
  updateWinAsarIntegrity({
    executablePath: executable,
    expectedValue: asarArchiveHeaderSha256({ asar, archivePath: original }),
    resedit,
  })
}
console.log(`portable ASAR repacked: preserved ${previous.length} original unpacked files`)
