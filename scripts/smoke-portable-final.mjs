#!/usr/bin/env node
/** Run the unchanged official smoke against the independently sealed final distribution. */
import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { createRequire } from 'node:module'
import { readFileSync, realpathSync } from 'node:fs'
import { basename, dirname, isAbsolute, join, relative } from 'node:path'
import { pathToFileURL } from 'node:url'
import { asarArchiveHeaderSha256, loadResedit, verifyWinAsarIntegrity } from './windows-portable-integrity.mjs'

const [stageArgument, applicationArgument, descriptorArgument] = process.argv.slice(2)
if (!stageArgument || !applicationArgument || !descriptorArgument) {
  throw new Error('usage: smoke-portable-final.mjs <official-stage> <application.app|win-unpacked> <final-descriptor/desktop-runtime.json>')
}
const stage = realpathSync(stageArgument)
const application = realpathSync(applicationArgument)
const descriptorPath = realpathSync(descriptorArgument)
for (const path of [application, descriptorPath]) {
  const suffix = relative(stage, path)
  assert.ok(suffix && suffix !== '..' && !suffix.startsWith(`..${process.platform === 'win32' ? '\\' : '/'}`) && !isAbsolute(suffix), 'smoke inputs must remain in the isolated build stage')
}
assert.equal(basename(descriptorPath), 'desktop-runtime.json')
const { readDesktopRuntime } = await import(pathToFileURL(join(stage, 'apps/desktop/src/runtime-tree.ts')).href)
const { smokePreparedRuntime } = await import(pathToFileURL(join(stage, 'apps/desktop/scripts/smoke-prepared-runtime.ts')).href)
const descriptor = readDesktopRuntime(dirname(descriptorPath))
const version = JSON.parse(readFileSync(join(stage, 'package.json'), 'utf8')).version
assert.equal(descriptor.release.version, version)
let resources
let executable
if (descriptor.platform === 'darwin' && descriptor.arch === 'arm64') {
  assert.equal(process.platform, 'darwin', 'macOS portable smoke requires a darwin host')
  execFileSync('/usr/bin/codesign', ['--verify', '--deep', '--strict', application], { stdio: 'inherit' })
  resources = join(application, 'Contents', 'Resources')
  executable = join(application, 'Contents', 'MacOS', 'DeepSeek Harness')
} else if (descriptor.platform === 'win32' && descriptor.arch === 'x64') {
  assert.equal(process.platform, 'win32', 'Windows portable smoke requires a win32 host')
  resources = join(application, 'resources')
  executable = join(application, 'DeepSeek Harness.exe')
  const asar = createRequire(join(stage, 'package.json'))(join(stage, 'node_modules/.pnpm/node_modules/@electron/asar'))
  const archive = join(resources, 'app.asar')
  const expectedValue = asarArchiveHeaderSha256({ asar, archivePath: archive })
  verifyWinAsarIntegrity({ executablePath: executable, expectedValue, resedit: loadResedit(stage), stageRoot: stage })
} else {
  throw new Error(`unsupported smoke target ${descriptor.platform}-${descriptor.arch}`)
}
await smokePreparedRuntime(join(resources, 'app.asar', 'dsh'), executable, join(resources, 'runtime'), descriptor)
console.log(JSON.stringify({ ok: true, version, platform: descriptor.platform, arch: descriptor.arch, finalArchiveIntegrity: true, packagedRuntimeSmoke: true }))
