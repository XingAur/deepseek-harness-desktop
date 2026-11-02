#!/usr/bin/env node
/** Audit permitted post-packaging changes before using the unmodified official seal/verifier. */
import assert from 'node:assert/strict'
import { createHash, randomUUID } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { existsSync, lstatSync, readdirSync, readFileSync, writeFileSync, chmodSync, mkdirSync, mkdtempSync, realpathSync, rmSync, linkSync, openSync, closeSync, fsyncSync } from 'node:fs'
import { createRequire } from 'node:module'
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { asarArchiveHeaderSha256, loadResedit, updateWinAsarIntegrity, verifyWinAsarIntegrity } from './windows-portable-integrity.mjs'

const DESCRIPTOR = 'desktop-runtime.json'
const TEXT_FILE = /\.(?:cjs|cmd|css|html|js|json|map|mjs|ps1|ts|txt|yaml|yml)$/iu
const SHIM_FILE = /^(?:pn|pnx|pnpm|pnpx)$/iu
const scriptDirectory = dirname(fileURLToPath(import.meta.url))
const hash = bytes => createHash('sha256').update(bytes).digest('hex')
const stableJson = value => `${JSON.stringify(value, undefined, 2)}\n`
const sorted = entries => entries.sort((a, b) => a.path < b.path ? -1 : a.path > b.path ? 1 : 0)
function pathExists(path) {
  try { lstatSync(path); return true } catch (error) {
    if (error.code === 'ENOENT') return false
    throw error
  }
}

export function safeRuntimePath(root, name) {
  if (typeof name !== 'string' || !name || isAbsolute(name) || name.includes('\\') || name.includes(':')
    || name.split('/').some(part => !part || part === '.' || part === '..')) throw new Error(`invalid runtime path: ${JSON.stringify(name)}`)
  return join(root, ...name.split('/'))
}

function contained(root, path) {
  const subpath = relative(root, path)
  return subpath === '' || (!isAbsolute(subpath) && subpath !== '..' && !subpath.startsWith(`..${sep}`))
}
function forbiddenApplicationPath(path) { return /^\/Applications(?:\/|$)/i.test(path) }

/** Resolve through existing ancestors so missing output paths cannot hide a symlink escape. */
export function checkedStagePath(stageRoot, candidate, { mustExist = false } = {}) {
  const stage = realpathSync(stageRoot)
  const path = resolve(candidate)
  if (forbiddenApplicationPath(stage) || forbiddenApplicationPath(path) || !contained(stage, path)) throw new Error('writable path must stay inside the disposable stage and outside /Applications')
  let ancestor = path
  while (!pathExists(ancestor)) {
    if (mustExist || ancestor === dirname(ancestor)) throw new Error(`required path is missing: ${path}`)
    ancestor = dirname(ancestor)
  }
  const physical = resolve(realpathSync(ancestor), relative(ancestor, path))
  if (!contained(stage, physical) || forbiddenApplicationPath(physical)) throw new Error('writable path escapes stage through a symlink')
  return physical
}

/** Match the existing sanitizer exactly, including its root ordering and filename filter. */
export function portableSanitizedBytes(name, original, forbiddenRoots) {
  if (!(TEXT_FILE.test(name) || SHIM_FILE.test(basename(name)))) return original
  const text = original.toString('utf8')
  if (!Buffer.from(text).equals(original)) throw new Error(`non-UTF8 sanitizer candidate: ${name}`)
  let output = text
  for (const root of forbiddenRoots.map(value => resolve(value))) {
    for (const value of new Set([root, root.replaceAll('\\', '/'), root.replaceAll('/', '\\')])) {
      output = output.replaceAll(value, '<portable-build-root>')
    }
  }
  return Buffer.from(output)
}

/** Only thin little-endian Mach-O 64 with one bounded LC_CODE_SIGNATURE is admitted. */
export function codeSignatureRange(bytes) {
  if (bytes.length < 32 || bytes.readUInt32LE(0) !== 0xfeedfacf) throw new Error('not a thin little-endian Mach-O 64 binary')
  const count = bytes.readUInt32LE(16)
  const commandBytes = bytes.readUInt32LE(20)
  const end = 32 + commandBytes
  if (!count || end > bytes.length || count > commandBytes / 8) throw new Error('invalid Mach-O load command extent')
  let cursor = 32
  let signature
  for (let index = 0; index < count; index++) {
    if (cursor + 8 > end) throw new Error('truncated Mach-O load command')
    const command = bytes.readUInt32LE(cursor)
    const size = bytes.readUInt32LE(cursor + 4)
    if (size < 8 || size % 8 || cursor + size > end) throw new Error('invalid Mach-O load command size')
    if (command === 0x1d) {
      if (signature || size !== 16) throw new Error('ambiguous Mach-O code signature command')
      const offset = bytes.readUInt32LE(cursor + 8)
      const length = bytes.readUInt32LE(cursor + 12)
      if (!length || offset < end || offset + length > bytes.length) throw new Error('invalid Mach-O code signature range')
      signature = { offset, bytes: length }
    }
    cursor += size
  }
  if (cursor !== end || !signature) throw new Error('missing or malformed Mach-O code signature command')
  return signature
}

/** Returns a permitted byte transformation; it never treats a new checksum as authorization. */
export function classifyRuntimeChange(name, original, final, forbiddenRoots, platform = 'darwin') {
  if (original.equals(final)) return { kind: 'unchanged' }
  if (TEXT_FILE.test(name) || SHIM_FILE.test(basename(name))) {
    const expected = portableSanitizedBytes(name, original, forbiddenRoots)
    if (!expected.equals(original) && final.equals(expected)) return { kind: 'exact-path-sanitization' }
  }
  if (platform === 'win32') throw new Error(`Windows runtime native or opaque bytes changed: ${name}`)
  const before = codeSignatureRange(original)
  const after = codeSignatureRange(final)
  if (original.length !== final.length || before.offset !== after.offset || before.bytes !== after.bytes) throw new Error(`Mach-O signature extent changed: ${name}`)
  const end = before.offset + before.bytes
  if (!original.subarray(0, before.offset).equals(final.subarray(0, before.offset))
    || !original.subarray(end).equals(final.subarray(end))) throw new Error(`Mach-O bytes outside LC_CODE_SIGNATURE changed: ${name}`)
  return { kind: 'mach-o-signature-only', signature: before }
}

/** Portable executable permissions use the same meaning as the official inventory. */
export function inventoryForAudit(root, { platform = 'darwin' } = {}) {
  const files = []
  const visit = (directory, prefix = '') => {
    const directoryStat = lstatSync(directory)
    if (!directoryStat.isDirectory() || directoryStat.isSymbolicLink()) throw new Error(`unexpected directory entry: ${directory}`)
    for (const name of readdirSync(directory)) {
      const path = join(directory, name)
      const logical = prefix ? `${prefix}/${name}` : name
      const stat = lstatSync(path)
      if (stat.isDirectory()) visit(path, logical)
      else if (!stat.isFile() || stat.isSymbolicLink()) throw new Error(`unsupported runtime entry: ${logical}`)
      else if (logical !== DESCRIPTOR) {
        safeRuntimePath(root, logical)
        const body = readFileSync(path)
        files.push({ path: logical, bytes: body.length, sha256: hash(body), executable: platform === 'win32' ? false : (stat.mode & 0o111) !== 0 })
      }
    }
  }
  visit(root)
  return sorted(files)
}

export function assertSameMembershipAndMode(expected, actual) {
  const entries = files => {
    const map = new Map()
    for (const entry of files) {
      safeRuntimePath('/', entry.path)
      if (map.has(entry.path) || typeof entry.executable !== 'boolean') throw new Error('invalid inventory membership or mode')
      map.set(entry.path, entry)
    }
    return map
  }
  const old = entries(expected)
  const next = entries(actual)
  if (old.size !== next.size || [...old.keys()].some(name => !next.has(name))) throw new Error('runtime file membership changed')
  for (const [name, entry] of old) {
    if (next.get(name).executable !== entry.executable) throw new Error(`runtime executable mode changed: ${name}`)
  }
}

/** Audit every changed file against the verified original, requiring a native signature verifier. */
export async function auditRuntimeTrees({ originalRoot, finalRoot, originalDescriptor, forbiddenRoots, inventory, sanitizerReferenceRoot, verifyNativeSignature, platform = 'darwin' }) {
  const actual = inventory ?? inventoryForAudit(finalRoot, { platform })
  assertSameMembershipAndMode(originalDescriptor.files, actual)
  const changes = []
  const original = new Map(originalDescriptor.files.map(entry => [entry.path, entry]))
  for (const current of actual) {
    const before = original.get(current.path)
    if (before.bytes === current.bytes && before.sha256 === current.sha256) continue
    const oldBytes = readFileSync(safeRuntimePath(originalRoot, current.path))
    const newPath = safeRuntimePath(finalRoot, current.path)
    const newBytes = readFileSync(newPath)
    if (hash(oldBytes) !== before.sha256 || oldBytes.length !== before.bytes) throw new Error(`original changed during audit: ${current.path}`)
    if (hash(newBytes) !== current.sha256 || newBytes.length !== current.bytes) throw new Error(`candidate changed during audit: ${current.path}`)
    const transformation = classifyRuntimeChange(current.path, oldBytes, newBytes, forbiddenRoots, platform)
    if (transformation.kind === 'exact-path-sanitization' && sanitizerReferenceRoot !== undefined
      && !readFileSync(safeRuntimePath(sanitizerReferenceRoot, current.path)).equals(newBytes)) throw new Error(`existing sanitizer disagrees: ${current.path}`)
    if (transformation.kind === 'mach-o-signature-only') {
      if (typeof verifyNativeSignature !== 'function') throw new Error('native signature verification is required')
      await verifyNativeSignature(newPath)
    }
    changes.push({ path: current.path, ...transformation, before: { bytes: before.bytes, sha256: before.sha256 }, after: { bytes: current.bytes, sha256: current.sha256 }, executable: current.executable })
  }
  return { files: actual, changes }
}

/** Reject archive links, traversal, membership/execute changes and unlisted physical unpacked files. */
export function auditArchiveLayout(asar, archive, expected, { platform = 'darwin' } = {}) {
  const header = asar.getRawHeader(archive).header
  const physical = new Set()
  const inventory = []
  const visit = (node, name = '') => {
    if (name) safeRuntimePath('/', name)
    if (node.link !== undefined) throw new Error(`ASAR links are not permitted: ${name}`)
    if (node.files !== undefined) {
      for (const [part, child] of Object.entries(node.files)) visit(child, name ? `${name}/${part}` : part)
      return
    }
    const isRuntime = name.startsWith('dsh/')
    let executable = platform === 'win32' ? false : node.executable === true
    if (node.unpacked === true) {
      const path = safeRuntimePath(`${archive}.unpacked`, name)
      const stat = lstatSync(path)
      if (!stat.isFile() || stat.isSymbolicLink()) throw new Error(`unexpected physical unpacked file: ${name}`)
      physical.add(path)
      if (platform !== 'win32') executable = (stat.mode & 0o111) !== 0
    }
    if (isRuntime && name !== `dsh/${DESCRIPTOR}`) inventory.push({ path: name.slice(4), executable })
  }
  if (!header.files?.dsh?.files) throw new Error('ASAR is missing the dsh runtime')
  visit(header)
  assertSameMembershipAndMode(expected.files, inventory)
  const inspectPhysical = directory => {
    const stat = lstatSync(directory)
    if (!stat.isDirectory() || stat.isSymbolicLink()) throw new Error('unexpected unpacked directory link')
    for (const entry of readdirSync(directory)) {
      const path = join(directory, entry)
      const entryStat = lstatSync(path)
      if (entryStat.isDirectory()) inspectPhysical(path)
      else if (!entryStat.isFile() || entryStat.isSymbolicLink() || !physical.has(path)) throw new Error(`unlisted unpacked file: ${path}`)
    }
  }
  if (existsSync(`${archive}.unpacked`)) inspectPhysical(`${archive}.unpacked`)
}

/** Preserve verified physical unpacked permissions that extractAll may omit from its copies. */
export function restoreUnpackedExtractionModes(asar, archive, extracted, expected, { platform = 'darwin' } = {}) {
  // This is not a repair of untrusted input: reject an incorrect input mode
  // before touching any extracted file, then reproduce that verified input.
  auditArchiveLayout(asar, archive, expected, { platform })
  if (platform === 'win32') return 0
  const root = realpathSync(extracted)
  let restored = 0
  const visit = (node, name = '') => {
    if (node.files !== undefined) {
      for (const [part, child] of Object.entries(node.files)) visit(child, name ? `${name}/${part}` : part)
    } else if (node.unpacked === true) {
      const source = safeRuntimePath(`${archive}.unpacked`, name)
      const destination = safeRuntimePath(root, name)
      const before = lstatSync(source)
      const after = lstatSync(destination)
      if (!after.isFile() || after.isSymbolicLink() || !contained(root, realpathSync(destination))) throw new Error('invalid unpacked extraction target')
      if ((after.mode & 0o111) !== (before.mode & 0o111)) {
        chmodSync(destination, (after.mode & 0o666) | (before.mode & 0o111))
        restored++
      }
    }
  }
  visit(asar.getRawHeader(archive).header)
  return restored
}

/** A repeated run may only reuse sidecars that match its independently repeated byte audit. */
export function assertFinalSidecarsMatch(finalDescriptorBytes, audit, existingDescriptorBytes, existingAuditBytes) {
  if (!existingDescriptorBytes.equals(finalDescriptorBytes)) throw new Error('existing final descriptor disagrees with a fresh original-to-final audit')
  assert.deepEqual(JSON.parse(existingAuditBytes.toString('utf8')), audit, 'existing audit sidecar disagrees with fresh byte audit')
}

/** Publish a new sidecar atomically without overwriting even a concurrent existing file. */
function publishSidecar(stage, destination, bytes) {
  const path = checkedStagePath(stage, destination)
  mkdirSync(dirname(path), { recursive: true })
  checkedStagePath(stage, dirname(path), { mustExist: true })
  const temporary = checkedStagePath(stage, join(dirname(path), `.${basename(path)}.${randomUUID()}.tmp`))
  let descriptor
  try {
    descriptor = openSync(temporary, 'wx', 0o600)
    writeFileSync(descriptor, bytes)
    fsyncSync(descriptor)
    closeSync(descriptor); descriptor = undefined
    linkSync(temporary, path)
  } finally {
    if (descriptor !== undefined) closeSync(descriptor)
    checkedStagePath(stage, temporary)
    rmSync(temporary, { force: true })
  }
}

const PORTABLE_PLATFORMS = {
  'darwin-arm64': { platform: 'darwin', arch: 'arm64', prepared: 'apps/desktop/.desktop-build/targets/mac-arm64/dsh', asar: ['Contents', 'Resources', 'app.asar'] },
  'win32-x64': { platform: 'win32', arch: 'x64', prepared: 'apps/desktop/.desktop-build/targets/win-x64/dsh', asar: ['resources', 'app.asar'], executable: 'DeepSeek Harness.exe' },
}

/** Infer darwin-arm64 vs win32-x64 from the unpacked application layout. */
export function inferPortablePlatform(app) {
  const mac = pathExists(join(app, 'Contents', 'Resources', 'app.asar'))
  const win = pathExists(join(app, 'resources', 'app.asar')) && pathExists(join(app, 'DeepSeek Harness.exe'))
  if (mac && !win) return 'darwin-arm64'
  if (win && !mac) return 'win32-x64'
  throw new Error('unable to infer portable platform from application layout')
}

export async function finalizePortableRuntime({ stageRoot, appPath, sourceRoot, descriptorOutput, platform: platformId } = {}) {
  const stage = realpathSync(stageRoot)
  if (forbiddenApplicationPath(stage)) throw new Error('never finalize an installed application')
  const app = checkedStagePath(stage, appPath, { mustExist: true })
  const resolvedPlatform = platformId ?? inferPortablePlatform(app)
  const spec = PORTABLE_PLATFORMS[resolvedPlatform]
  if (!spec) throw new Error(`unsupported portable platform: ${resolvedPlatform}`)
  if (resolvedPlatform === 'darwin-arm64' && process.platform !== 'darwin') throw new Error('finalization requires macOS codesign and PlistBuddy')
  if (resolvedPlatform === 'win32-x64' && process.platform !== 'win32') throw new Error('Windows finalization requires a Windows host')
  const prepared = checkedStagePath(stage, join(stage, spec.prepared), { mustExist: true })
  const archive = checkedStagePath(stage, join(app, ...spec.asar), { mustExist: true })
  checkedStagePath(stage, `${archive}.unpacked`, { mustExist: true })
  const executable = spec.executable ? checkedStagePath(stage, join(app, spec.executable), { mustExist: true }) : undefined
  const output = checkedStagePath(stage, descriptorOutput)
  const auditOutput = checkedStagePath(stage, `${output}.audit.json`)
  if (basename(output) !== DESCRIPTOR || contained(app, output) || contained(prepared, output)) throw new Error('descriptor output must be an independent stage directory ending in desktop-runtime.json')
  for (const path of [output, auditOutput]) {
    if (pathExists(path) && (!lstatSync(path).isFile() || lstatSync(path).isSymbolicLink())) throw new Error('sidecar must be a regular file, not a link')
  }
  const repeated = pathExists(output)
  if (repeated !== pathExists(auditOutput)) throw new Error('incomplete finalization sidecar pair; refusing to overwrite')
  const originalDescriptorPath = join(prepared, DESCRIPTOR)
  const originalDescriptorBytes = readFileSync(originalDescriptorPath)
  const runtimeTree = await import(pathToFileURL(join(stage, 'apps/desktop/src/runtime-tree.ts')).href)
  const { verifyRuntimeArchive } = await import(pathToFileURL(join(stage, 'apps/desktop/scripts/verify-runtime-archive.ts')).href)
  const declared = JSON.parse(originalDescriptorBytes.toString('utf8'))
  const target = { platform: spec.platform, arch: spec.arch }
  const originalDescriptor = await runtimeTree.verifyDesktopRuntime(prepared, declared.release.version, target)
  if (!originalDescriptorBytes.equals(Buffer.from(stableJson(originalDescriptor)))) throw new Error('original descriptor is not the official canonical descriptor')
  const moduleRoot = join(stage, 'node_modules/.pnpm/node_modules/@electron/asar')
  const asar = createRequire(join(stage, 'package.json'))(moduleRoot)
  auditArchiveLayout(asar, archive, originalDescriptor, { platform: target.platform })
  const archivedDescriptor = asar.extractFile(archive, `dsh/${DESCRIPTOR}`)
  const expectedDescriptorBytes = repeated ? readFileSync(output) : originalDescriptorBytes
  if (!archivedDescriptor.equals(expectedDescriptorBytes)) throw new Error('archived descriptor differs from the verified preparation or existing sidecar')
  const scratch = realpathSync(mkdtempSync(join(stage, '.finalize-portable-runtime-')))
  const scratchParent = realpathSync(stage)
  const clean = () => {
    const actual = realpathSync(scratch)
    assert.equal(actual, scratch)
    assert.equal(dirname(actual), scratchParent)
    assert.ok(basename(actual).startsWith('.finalize-portable-runtime-'))
    rmSync(actual, { recursive: true, force: true })
  }
  try {
    const extracted = join(scratch, 'archive')
    asar.extractAll(archive, extracted)
    restoreUnpackedExtractionModes(asar, archive, extracted, originalDescriptor, { platform: target.platform })
    const finalRoot = join(extracted, 'dsh')
    const inventory = inventoryForAudit(finalRoot, { platform: target.platform })
    assertSameMembershipAndMode(originalDescriptor.files, inventory)
    const originalFiles = new Map(originalDescriptor.files.map(entry => [entry.path, entry]))
    const comparison = join(scratch, 'sanitizer-reference')
    mkdirSync(comparison)
    for (const entry of inventory) {
      if (originalFiles.get(entry.path).sha256 === entry.sha256) continue
      if (!(TEXT_FILE.test(entry.path) || SHIM_FILE.test(basename(entry.path)))) continue
      const path = safeRuntimePath(comparison, entry.path)
      mkdirSync(dirname(path), { recursive: true })
      writeFileSync(path, readFileSync(safeRuntimePath(prepared, entry.path)))
    }
    // Use the existing sanitizer, not an independently relaxed post-processing rule.
    execFileSync(process.execPath, [join(scriptDirectory, 'sanitize-portable-paths.mjs'), comparison, resolve(sourceRoot), resolve(stageRoot)], { stdio: 'inherit' })
    const audited = await auditRuntimeTrees({
      originalRoot: prepared, finalRoot, originalDescriptor, forbiddenRoots: [sourceRoot, stageRoot], inventory,
      sanitizerReferenceRoot: comparison, platform: target.platform,
      verifyNativeSignature: target.platform === 'darwin'
        ? path => execFileSync('/usr/bin/codesign', ['--verify', '--strict', path], { stdio: 'inherit' })
        : undefined,
    })
    // Only after every mutation has been justified may the official writer reseal.
    const finalDescriptor = runtimeTree.writeDesktopRuntime(finalRoot, originalDescriptor.release,
      originalDescriptor.sharedPackages.map(entry => entry.name), target)
    assert.deepEqual(finalDescriptor.files, audited.files, 'candidate bytes changed between audit and official sealing')
    assert.deepEqual(finalDescriptor.sharedPackages, originalDescriptor.sharedPackages, 'shared package metadata must not change')
    assert.deepEqual(finalDescriptor.release, originalDescriptor.release)
    await runtimeTree.verifyDesktopRuntime(finalRoot, originalDescriptor.release.version, target)
    const finalDescriptorBytes = readFileSync(join(finalRoot, DESCRIPTOR))
    const audit = {
      schemaVersion: 1, status: 'verified', originalDescriptorSha256: hash(originalDescriptorBytes), finalDescriptorSha256: hash(finalDescriptorBytes),
      platform: target.platform, arch: target.arch, fileCount: audited.files.length,
      changes: audited.changes,
      counts: {
        exactPathSanitization: audited.changes.filter(entry => entry.kind === 'exact-path-sanitization').length,
        nativeSignatureOnly: audited.changes.filter(entry => entry.kind === 'mach-o-signature-only').length,
      },
    }
    if (repeated) {
      assertFinalSidecarsMatch(finalDescriptorBytes, audit, readFileSync(output), readFileSync(auditOutput))
    } else {
      for (const path of [`${archive}.sanitized`, `${archive}.sanitized.unpacked`]) {
        checkedStagePath(stage, path)
        if (pathExists(path)) throw new Error('stale repack output exists; refusing to delete it implicitly')
      }
      execFileSync(process.execPath, [join(scriptDirectory, 'repack-portable-asar.mjs'), moduleRoot, archive, extracted, resolvedPlatform], { stdio: 'inherit' })
      if (target.platform === 'darwin') {
        // Never --deep-sign here: that would invalidate the newly recorded native bytes.
        execFileSync('/usr/bin/codesign', ['--force', '--sign', '-', '--preserve-metadata=entitlements,requirements,flags', app], { stdio: 'inherit' })
      } else {
        const expectedValue = asarArchiveHeaderSha256({ asar, archivePath: archive })
        updateWinAsarIntegrity({ executablePath: executable, expectedValue, resedit: loadResedit(stage), stageRoot: stage })
      }
    }
    if (target.platform === 'darwin') {
      execFileSync('/usr/bin/codesign', ['--verify', '--deep', '--strict', app], { stdio: 'inherit' })
    }
    await verifyRuntimeArchive(archive, finalDescriptor)
    asar.uncache(archive)
    const headerHash = hash(Buffer.from(asar.getRawHeader(archive).headerString))
    if (target.platform === 'darwin') {
      const plistHash = execFileSync('/usr/libexec/PlistBuddy', ['-c', 'Print :ElectronAsarIntegrity:Resources/app.asar:hash', join(app, 'Contents/Info.plist')], { encoding: 'utf8' }).trim()
      if (headerHash !== plistHash) throw new Error('Info.plist ASAR integrity digest differs from final archive')
    } else {
      verifyWinAsarIntegrity({ executablePath: executable, expectedValue: headerHash, resedit: loadResedit(stage), stageRoot: stage })
    }
    if (!readFileSync(originalDescriptorPath).equals(originalDescriptorBytes)) throw new Error('original prepared descriptor changed during finalization')
    if (!repeated) {
      // These independent sidecars only appear after signing and the original
      // verifier succeed; neither existing output is ever overwritten.
      publishSidecar(stage, auditOutput, Buffer.from(stableJson(audit)))
      publishSidecar(stage, output, finalDescriptorBytes)
    }
    console.log(`portable runtime ${repeated ? 'revalidated (no-op)' : 'finalized'}: ${audit.counts.exactPathSanitization} exact text replacements, ${audit.counts.nativeSignatureOnly} signature-only binaries`)
    console.log(`verified descriptor: ${output}\nchange audit: ${auditOutput}`)
    return { descriptorOutput: output, auditOutput, repeated, audit }
  } finally { clean() }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [stageRoot, appPath, sourceRoot, descriptorOutput, platform, ...extra] = process.argv.slice(2)
  if (!stageRoot || !appPath || !sourceRoot || !descriptorOutput || extra.length) {
    throw new Error('usage: finalize-portable-runtime.mjs <stageRoot> <appPath> <sourceRoot> <stage/.portable-final/desktop-runtime.json> [darwin-arm64|win32-x64]')
  }
  if (platform && !PORTABLE_PLATFORMS[platform]) throw new Error('usage: finalize-portable-runtime.mjs <stageRoot> <appPath> <sourceRoot> <stage/.portable-final/desktop-runtime.json> [darwin-arm64|win32-x64]')
  await finalizePortableRuntime({ stageRoot, appPath, sourceRoot, descriptorOutput, platform })
}
