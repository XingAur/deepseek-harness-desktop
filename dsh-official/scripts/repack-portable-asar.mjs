#!/usr/bin/env node
// Preserve electron-builder's native unpacking while sanitizing a disposable ASAR.
import { existsSync, renameSync, rmSync } from 'node:fs'
import { createRequire } from 'node:module'
import { join, resolve } from 'node:path'

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

rmSync(output, { force: true })
rmSync(outputUnpacked, { recursive: true, force: true })
await asar.createPackageWithOptions(resolve(extracted), output, { unpackDir: join('dsh', 'node_modules') })
for (const file of previous) {
  if (asar.statFile(output, join(...file.split('/'))).unpacked !== true || !existsSync(join(outputUnpacked, file))) {
    throw new Error(`ASAR repack lost an unpacked native dependency: ${file}`)
  }
}
rmSync(original, { force: true })
rmSync(originalUnpacked, { recursive: true, force: true })
renameSync(output, original)
renameSync(outputUnpacked, originalUnpacked)
console.log(`portable ASAR repacked: preserved ${previous.length} original unpacked files`)
