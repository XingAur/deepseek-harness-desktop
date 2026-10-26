#!/usr/bin/env node
import assert from 'node:assert/strict'
import test from 'node:test'
import { execFileSync } from 'node:child_process'
import { mkdtempSync, mkdirSync, writeFileSync, readFileSync, lstatSync, chmodSync, cpSync, realpathSync, rmSync, symlinkSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, dirname, join, resolve } from 'node:path'
import { createRequire } from 'node:module'
import { fileURLToPath, pathToFileURL } from 'node:url'
import {
  assertFinalSidecarsMatch, assertSameMembershipAndMode, auditArchiveLayout, auditRuntimeTrees,
  checkedStagePath, classifyRuntimeChange, codeSignatureRange, inferPortablePlatform, inventoryForAudit,
  portableSanitizedBytes, restoreUnpackedExtractionModes, safeRuntimePath,
} from './finalize-portable-runtime.mjs'

function owned() {
  const parent = realpathSync(tmpdir())
  const root = realpathSync(mkdtempSync(join(parent, 'dsh-finalize-fixture-')))
  return {
    root,
    clean() {
      const actual = realpathSync(root)
      assert.equal(actual, root)
      assert.equal(dirname(actual), parent)
      assert.ok(basename(actual).startsWith('dsh-finalize-fixture-'))
      rmSync(actual, { recursive: true, force: true })
    },
  }
}
function put(root, path, bytes, mode = 0o644) {
  const full = join(root, path)
  mkdirSync(dirname(full), { recursive: true })
  writeFileSync(full, bytes, { mode })
  return full
}
function macho(signatureByte = 0x11) {
  const bytes = Buffer.alloc(256)
  bytes.writeUInt32LE(0xfeedfacf, 0)
  bytes.writeUInt32LE(0x0100000c, 4)
  bytes.writeUInt32LE(2, 12)
  bytes.writeUInt32LE(1, 16)
  bytes.writeUInt32LE(16, 20)
  bytes.writeUInt32LE(0x1d, 32)
  bytes.writeUInt32LE(16, 36)
  bytes.writeUInt32LE(128, 40)
  bytes.writeUInt32LE(64, 44)
  bytes.write('immutable-code-and-data', 64)
  bytes.fill(signatureByte, 128, 192)
  bytes.write('immutable-suffix', 200)
  return bytes
}
function trees() {
  const temporary = owned()
  const originalRoot = join(temporary.root, 'prepared')
  const finalRoot = join(temporary.root, 'final')
  const forbiddenRoots = [join(temporary.root, 'source'), join(temporary.root, 'source/stage')]
  const script = Buffer.from(`const ROOT = ${JSON.stringify(forbiddenRoots[0])};\n`)
  put(originalRoot, 'main.js', script)
  put(originalRoot, 'native/addon.node', macho(), 0o755)
  put(originalRoot, 'unchanged.txt', 'preserve\n')
  cpSync(originalRoot, finalRoot, { recursive: true })
  const originalDescriptor = { files: inventoryForAudit(originalRoot) }
  return { ...temporary, originalRoot, finalRoot, forbiddenRoots, originalDescriptor }
}

// Every test below writes only a fresh owned temporary tree. The optional real
// official-module test never launches, repacks or signs a real application.
test('text authorization exactly follows the existing sanitizer, including filename selection and root order', () => {
  const f = owned()
  try {
    const roots = [join(f.root, 'source'), join(f.root, 'source/stage')]
    const body = Buffer.from(`${roots[1]}/x\n${roots[0].replaceAll('/', '\\')}\\y\n`)
    for (const name of ['main.js', 'config.JSON', 'pnpm', 'ignored.bin', 'senddoc']) put(f.root, `compare/${name}`, body)
    execFileSync(process.execPath, [fileURLToPath(new URL('./sanitize-portable-paths.mjs', import.meta.url)), join(f.root, 'compare'), ...roots], { stdio: 'pipe' })
    for (const name of ['main.js', 'config.JSON', 'pnpm', 'ignored.bin', 'senddoc']) {
      assert.ok(readFileSync(join(f.root, 'compare', name)).equals(portableSanitizedBytes(name, body, roots)), name)
    }
    assert.equal(classifyRuntimeChange('main.js', body, portableSanitizedBytes('main.js', body, roots), roots).kind, 'exact-path-sanitization')
    assert.throws(() => classifyRuntimeChange('ignored.bin', body, Buffer.from('changed'), roots))
    assert.throws(() => portableSanitizedBytes('main.js', Buffer.from([0xff, 0x00]), roots), /non-UTF8/)
  } finally { f.clean() }
})

test('body tampering, appended code and partial path replacement are never authorized as text sanitization', () => {
  const original = Buffer.from('const root="/fixture/source";\nconst keep=true;\n')
  const expected = portableSanitizedBytes('main.js', original, ['/fixture/source'])
  assert.equal(classifyRuntimeChange('main.js', original, original, ['/fixture/source']).kind, 'unchanged')
  for (const changed of [Buffer.concat([expected, Buffer.from('runInjectedCode();')]), Buffer.from('const keep=false;'), Buffer.from('const root="<portable-build-root>";')]) {
    assert.throws(() => classifyRuntimeChange('main.js', original, changed, ['/fixture/source']))
  }
})

test('thin LE64 signature bytes alone are authorized, with the original extent retained', () => {
  const before = macho(0x11)
  const after = macho(0x22)
  assert.deepEqual(codeSignatureRange(before), { offset: 128, bytes: 64 })
  assert.deepEqual(classifyRuntimeChange('addon.node', before, after, []), { kind: 'mach-o-signature-only', signature: { offset: 128, bytes: 64 } })
})

test('native code, suffix, signature extent and file-size changes are rejected', () => {
  const before = macho()
  const codeChanged = macho(0x22); codeChanged[64] ^= 1
  const suffixChanged = macho(0x22); suffixChanged[220] ^= 1
  const offsetChanged = macho(0x22); offsetChanged.writeUInt32LE(120, 40)
  const sizeChanged = macho(0x22); sizeChanged.writeUInt32LE(56, 44)
  for (const after of [codeChanged, suffixChanged, offsetChanged, sizeChanged, Buffer.concat([macho(0x22), Buffer.alloc(1)])]) {
    assert.throws(() => classifyRuntimeChange('addon.node', before, after, []))
  }
})

test('fat/big-endian/32-bit, overlapping, missing and duplicate signature commands are rejected', () => {
  for (const magic of [0xcafebabe, 0xcffaedfe, 0xfeedface]) {
    const bytes = macho(); bytes.writeUInt32LE(magic, 0)
    assert.throws(() => codeSignatureRange(bytes), /thin little-endian/)
  }
  const missing = macho(); missing.writeUInt32LE(0x19, 32)
  const overlap = macho(); overlap.writeUInt32LE(32, 40)
  const overrun = macho(); overrun.writeUInt32LE(500, 44)
  const malformed = macho(); malformed.writeUInt32LE(15, 36)
  const duplicate = macho(); duplicate.writeUInt32LE(2, 16); duplicate.writeUInt32LE(32, 20)
  duplicate.set(duplicate.subarray(32, 48), 48)
  for (const bytes of [missing, overlap, overrun, malformed, duplicate]) assert.throws(() => codeSignatureRange(bytes))
})

test('full-tree audit accepts only exact text and signature changes and calls the native verifier', async () => {
  const f = trees()
  try {
    put(f.finalRoot, 'main.js', portableSanitizedBytes('main.js', readFileSync(join(f.originalRoot, 'main.js')), f.forbiddenRoots))
    put(f.finalRoot, 'native/addon.node', macho(0x22))
    const verified = []
    const result = await auditRuntimeTrees({ ...f, verifyNativeSignature: async path => { verified.push(path) } })
    assert.deepEqual(result.changes.map(change => change.kind), ['exact-path-sanitization', 'mach-o-signature-only'])
    assert.deepEqual(verified, [join(f.finalRoot, 'native/addon.node')])
    assert.deepEqual(result.files, inventoryForAudit(f.finalRoot))
  } finally { f.clean() }
})

test('signature-only bytes cannot pass without successful signature verification', async () => {
  const f = trees()
  try {
    put(f.finalRoot, 'native/addon.node', macho(0x22))
    await assert.rejects(auditRuntimeTrees(f), /verification is required/)
    await assert.rejects(auditRuntimeTrees({ ...f, verifyNativeSignature: () => { throw new Error('fixture invalid signature') } }), /fixture invalid signature/)
  } finally { f.clean() }
})

test('membership, executable mode and symlink changes are rejected before sealing', async () => {
  for (const kind of ['extra', 'missing', 'mode', 'link']) {
    const f = trees()
    try {
      if (kind === 'extra') put(f.finalRoot, 'injected.js', 'injected')
      if (kind === 'missing' || kind === 'link') {
        const path = realpathSync(join(f.finalRoot, 'unchanged.txt'))
        assert.equal(dirname(path), f.finalRoot)
        rmSync(path)
        if (kind === 'link') symlinkSync(join(f.originalRoot, 'unchanged.txt'), path)
      }
      if (kind === 'mode') chmodSync(join(f.finalRoot, 'main.js'), 0o755)
      await assert.rejects(auditRuntimeTrees(f), /membership|mode|unsupported/)
    } finally { f.clean() }
  }
})

test('original byte changes and a disagreeing existing-sanitizer result fail audit', async () => {
  const f = trees()
  try {
    const original = readFileSync(join(f.originalRoot, 'main.js'))
    put(f.finalRoot, 'main.js', portableSanitizedBytes('main.js', original, f.forbiddenRoots))
    const reference = join(f.root, 'comparison')
    put(reference, 'main.js', 'not the sanitizer result')
    await assert.rejects(auditRuntimeTrees({ ...f, sanitizerReferenceRoot: reference }), /sanitizer disagrees/)
    put(f.originalRoot, 'main.js', 'original changed after verification')
    await assert.rejects(auditRuntimeTrees(f), /original changed during audit/)
  } finally { f.clean() }
})

test('inventory identity rejects duplicates, traversal and mode coercion', () => {
  const expected = [{ path: 'main.js', executable: false }]
  assert.throws(() => assertSameMembershipAndMode(expected, [...expected, ...expected]), /invalid inventory/)
  assert.throws(() => assertSameMembershipAndMode(expected, [{ path: 'main.js', executable: 0 }]), /invalid inventory/)
  for (const path of ['', '../escape', '/absolute', 'a\\b', 'a:stream', 'a//b']) assert.throws(() => safeRuntimePath('/fixture', path), /invalid runtime path/)
})

test('stage output validation rejects siblings, /Applications and live or broken symlink escapes', () => {
  const f = owned()
  try {
    const stage = join(f.root, 'stage'); const outside = join(f.root, 'outside')
    mkdirSync(stage); mkdirSync(outside)
    assert.equal(checkedStagePath(stage, join(stage, '.portable-final/desktop-runtime.json')), join(stage, '.portable-final/desktop-runtime.json'))
    mkdirSync(join(stage, 'prepared'))
    symlinkSync(join(stage, 'prepared'), join(stage, 'prepared-alias'))
    assert.equal(checkedStagePath(stage, join(stage, 'prepared-alias/desktop-runtime.json')), join(stage, 'prepared/desktop-runtime.json'), 'protected-root exclusions must receive canonical physical paths')
    assert.throws(() => checkedStagePath(stage, join(outside, 'desktop-runtime.json')), /inside the disposable stage/)
    assert.throws(() => checkedStagePath(stage, '/Applications/Fixture.app'), /inside the disposable stage/)
    symlinkSync(outside, join(stage, 'escape'))
    assert.throws(() => checkedStagePath(stage, join(stage, 'escape/descriptor.json')), /symlink/)
    symlinkSync(join(outside, 'missing'), join(stage, 'broken'))
    assert.throws(() => checkedStagePath(stage, join(stage, 'broken/descriptor.json')))
  } finally { f.clean() }
})

test('archive inspection rejects links, traversal, changed executable state and unlisted unpacked files', () => {
  const f = owned()
  try {
    const archive = join(f.root, 'app.asar')
    const expected = { files: [{ path: 'main.js', executable: false }] }
    const makeAsar = node => ({ getRawHeader: () => ({ header: { files: { dsh: { files: { 'main.js': node, 'desktop-runtime.json': {} } } } } }) })
    auditArchiveLayout(makeAsar({}), archive, expected)
    assert.throws(() => auditArchiveLayout(makeAsar({ executable: true }), archive, expected), /mode/)
    assert.throws(() => auditArchiveLayout(makeAsar({ link: '/outside' }), archive, expected), /links/)
    const traversal = { getRawHeader: () => ({ header: { files: { dsh: { files: { '../evil': {} } } } } }) }
    assert.throws(() => auditArchiveLayout(traversal, archive, expected), /invalid runtime path/)
    put(`${archive}.unpacked`, 'dsh/main.js', 'fixture')
    auditArchiveLayout(makeAsar({ unpacked: true }), archive, expected)
    put(`${archive}.unpacked`, 'dsh/extra.js', 'unlisted')
    assert.throws(() => auditArchiveLayout(makeAsar({ unpacked: true }), archive, expected), /unlisted/)
  } finally { f.clean() }
})

test('extractAll mode loss is restored only from strictly validated physical input, never by relaxing its mode audit', () => {
  const f = owned()
  try {
    const archive = join(f.root, 'app.asar')
    const extracted = join(f.root, 'extracted')
    const physical = put(`${archive}.unpacked`, 'dsh/helper', 'fixture-helper', 0o755)
    const copy = put(extracted, 'dsh/helper', 'fixture-helper', 0o644)
    const asar = { getRawHeader: () => ({ header: { files: { dsh: { files: { helper: { unpacked: true } } } } } }) }
    const expected = { files: [{ path: 'helper', executable: true }] }
    assert.equal(restoreUnpackedExtractionModes(asar, archive, extracted, expected), 1)
    assert.equal(lstatSync(copy).mode & 0o777, 0o755)
    assert.equal(lstatSync(physical).mode & 0o777, 0o755)
    chmodSync(physical, 0o644)
    chmodSync(copy, 0o644)
    assert.throws(() => restoreUnpackedExtractionModes(asar, archive, extracted, expected), /mode changed/)
    assert.equal(lstatSync(copy).mode & 0o777, 0o644, 'invalid input must not cause a repair')
  } finally { f.clean() }
})

test('repeat validation accepts exact re-audited sidecars but rejects changed inventory or audit facts', () => {
  const descriptor = Buffer.from('{"files":[{"sha256":"fresh-audited"}]}\n')
  const audit = { schemaVersion: 1, finalDescriptorSha256: 'fresh-digest', changes: [{ path: 'main.js', kind: 'exact-path-sanitization' }] }
  const auditBytes = Buffer.from(JSON.stringify(audit))
  assertFinalSidecarsMatch(descriptor, audit, Buffer.from(descriptor), auditBytes)
  assert.throws(() => assertFinalSidecarsMatch(descriptor, audit, Buffer.from('{"files":[]}\n'), auditBytes), /descriptor disagrees/)
  assert.throws(() => assertFinalSidecarsMatch(descriptor, audit, descriptor, Buffer.from(JSON.stringify({ ...audit, changes: [] }))), /audit sidecar disagrees/)
})

function windowsTrees() {
  const temporary = owned()
  const originalRoot = join(temporary.root, 'prepared')
  const finalRoot = join(temporary.root, 'final')
  const forbiddenRoots = [join(temporary.root, 'source'), join(temporary.root, 'source/stage')]
  const script = Buffer.from(`const ROOT = ${JSON.stringify(forbiddenRoots[0])};\n`)
  put(originalRoot, 'main.js', script)
  put(originalRoot, 'native/addon.node', Buffer.from('PE-fake-native-bytes'))
  put(originalRoot, 'unchanged.txt', 'preserve\n')
  cpSync(originalRoot, finalRoot, { recursive: true })
  chmodSync(join(originalRoot, 'native/addon.node'), 0o755)
  chmodSync(join(finalRoot, 'native/addon.node'), 0o755)
  const originalDescriptor = { files: inventoryForAudit(originalRoot, { platform: 'win32' }) }
  return { ...temporary, originalRoot, finalRoot, forbiddenRoots, originalDescriptor }
}

test('Windows admits exact text sanitization and rejects PE or other opaque byte changes', async () => {
  const original = Buffer.from('const root="/fixture/source";\n')
  const expected = portableSanitizedBytes('main.js', original, ['/fixture/source'])
  assert.equal(classifyRuntimeChange('main.js', original, expected, ['/fixture/source'], 'win32').kind, 'exact-path-sanitization')
  assert.equal(classifyRuntimeChange('main.js', original, original, ['/fixture/source'], 'win32').kind, 'unchanged')
  assert.throws(() => classifyRuntimeChange('native/addon.node', Buffer.from('PE-a'), Buffer.from('PE-b'), [], 'win32'), /native or opaque/)
  assert.throws(() => classifyRuntimeChange('addon.node', macho(0x11), macho(0x22), [], 'win32'), /native or opaque/)
  const f = windowsTrees()
  try {
    put(f.finalRoot, 'main.js', portableSanitizedBytes('main.js', readFileSync(join(f.originalRoot, 'main.js')), f.forbiddenRoots))
    const result = await auditRuntimeTrees({ ...f, platform: 'win32' })
    assert.deepEqual(result.changes.map(change => change.kind), ['exact-path-sanitization'])
    put(f.finalRoot, 'native/addon.node', Buffer.from('PE-fake-native-bytes-changed'))
    await assert.rejects(auditRuntimeTrees({ ...f, platform: 'win32' }), /native or opaque/)
  } finally { f.clean() }
})

test('Windows inventory records executable false and does not fail on Unix mode bits', async () => {
  const f = windowsTrees()
  try {
    chmodSync(join(f.finalRoot, 'main.js'), 0o755)
    chmodSync(join(f.originalRoot, 'main.js'), 0o644)
    const original = inventoryForAudit(f.originalRoot, { platform: 'win32' })
    const actual = inventoryForAudit(f.finalRoot, { platform: 'win32' })
    assert.equal(original.every(entry => entry.executable === false), true)
    assert.equal(actual.every(entry => entry.executable === false), true)
    assertSameMembershipAndMode(original, actual)
    const result = await auditRuntimeTrees({ ...f, platform: 'win32' })
    assert.equal(result.changes.length, 0)
  } finally { f.clean() }
})

test('Windows membership changes are still rejected', async () => {
  for (const kind of ['extra', 'missing', 'link']) {
    const f = windowsTrees()
    try {
      if (kind === 'extra') put(f.finalRoot, 'injected.js', 'injected')
      if (kind === 'missing' || kind === 'link') {
        const path = realpathSync(join(f.finalRoot, 'unchanged.txt'))
        assert.equal(dirname(path), f.finalRoot)
        rmSync(path)
        if (kind === 'link') symlinkSync(join(f.originalRoot, 'unchanged.txt'), path)
      }
      await assert.rejects(auditRuntimeTrees({ ...f, platform: 'win32' }), /membership|unsupported/)
    } finally { f.clean() }
  }
})

test('Windows archive layout ignores Unix execute bits but still rejects extra unpacked files', () => {
  const f = owned()
  try {
    const archive = join(f.root, 'app.asar')
    const expected = { files: [{ path: 'main.js', executable: false }] }
    const makeAsar = node => ({ getRawHeader: () => ({ header: { files: { dsh: { files: { 'main.js': node, 'desktop-runtime.json': {} } } } } }) })
    auditArchiveLayout(makeAsar({ executable: true }), archive, expected, { platform: 'win32' })
    put(`${archive}.unpacked`, 'dsh/main.js', 'fixture', 0o755)
    auditArchiveLayout(makeAsar({ unpacked: true, executable: true }), archive, expected, { platform: 'win32' })
    put(`${archive}.unpacked`, 'dsh/extra.js', 'unlisted')
    assert.throws(() => auditArchiveLayout(makeAsar({ unpacked: true }), archive, expected, { platform: 'win32' }), /unlisted/)
  } finally { f.clean() }
})

test('portable platform inference distinguishes macOS app bundles from Windows unpacked dirs', () => {
  const f = owned()
  try {
    put(f.root, 'mac/Contents/Resources/app.asar', 'asar')
    put(f.root, 'win/resources/app.asar', 'asar')
    put(f.root, 'win/DeepSeek Harness.exe', 'exe')
    assert.equal(inferPortablePlatform(join(f.root, 'mac')), 'darwin-arm64')
    assert.equal(inferPortablePlatform(join(f.root, 'win')), 'win32-x64')
    assert.throws(() => inferPortablePlatform(f.root), /infer/)
  } finally { f.clean() }
})

test('official writer and unmodified archive verifier accept audited fixture bytes and reject subsequent tampering', { skip: !process.env.DSH_FINALIZE_TEST_STAGE && 'set DSH_FINALIZE_TEST_STAGE for official module integration' }, async () => {
  const stage = realpathSync(resolve(process.env.DSH_FINALIZE_TEST_STAGE))
  const runtime = await import(pathToFileURL(join(stage, 'apps/desktop/src/runtime-tree.ts')).href)
  const { verifyRuntimeArchive } = await import(pathToFileURL(join(stage, 'apps/desktop/scripts/verify-runtime-archive.ts')).href)
  const { DESKTOP_HOST_PROTOCOL_VERSION } = await import(pathToFileURL(join(stage, 'apps/desktop/src/host-protocol.ts')).href)
  const { DESKTOP_HOST_PACKAGE } = await import(pathToFileURL(join(stage, 'apps/desktop/src/core-package-set.ts')).href)
  const asar = createRequire(join(stage, 'package.json'))(join(stage, 'node_modules/.pnpm/node_modules/@electron/asar'))
  const f = owned()
  try {
    const prepared = join(f.root, 'prepared')
    const extracted = join(f.root, 'archive-input')
    const final = join(extracted, 'dsh')
    const shared = ['@deepseek-ai/dsh', DESKTOP_HOST_PACKAGE]
    const release = { schemaVersion: 1, version: '0.0.0-fixture.1', hostProtocolVersion: DESKTOP_HOST_PROTOCOL_VERSION, nodeVersion: '24.0.0', pnpmVersion: '10.0.0' }
    for (const name of shared) put(prepared, `node_modules/${name}/package.json`, JSON.stringify({ name, version: release.version }))
    const entry = 'node_modules/@deepseek-ai/dsh/index.js'
    put(prepared, entry, `export const root=${JSON.stringify(join(f.root, 'source'))};\n`)
    const target = { platform: 'darwin', arch: 'arm64' }
    const original = runtime.writeDesktopRuntime(prepared, release, shared, target)
    await runtime.verifyDesktopRuntime(prepared, release.version, target)
    const originalDescriptorBytes = readFileSync(join(prepared, 'desktop-runtime.json'))
    cpSync(prepared, final, { recursive: true })
    put(final, entry, portableSanitizedBytes(entry, readFileSync(join(prepared, entry)), [join(f.root, 'source')]))
    const audit = await auditRuntimeTrees({ originalRoot: prepared, finalRoot: final, originalDescriptor: original, forbiddenRoots: [join(f.root, 'source')] })
    assert.equal(audit.changes.length, 1)
    const sealed = runtime.writeDesktopRuntime(final, release, shared, target)
    assert.deepEqual(sealed.files, audit.files)
    await runtime.verifyDesktopRuntime(final, release.version, target)
    assert.ok(readFileSync(join(prepared, 'desktop-runtime.json')).equals(originalDescriptorBytes))
    const archive = join(f.root, 'fixture.asar')
    await asar.createPackageWithOptions(extracted, archive, { unpackDir: 'dsh/node_modules' })
    await verifyRuntimeArchive(archive, sealed)
    await assert.rejects(verifyRuntimeArchive(archive, original), /descriptor differs/)
    put(`${archive}.unpacked`, `dsh/${entry}`, 'tampered-after-seal')
    await assert.rejects(verifyRuntimeArchive(archive, sealed), /ASAR integrity verification failed/)
  } finally { f.clean() }
})
