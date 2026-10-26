#!/usr/bin/env node
/** JSON, PE-header and synthetic-resource tests. These do not claim a real Electron EXE passed on this host. */
import assert from 'node:assert/strict'
import test from 'node:test'
import { createRequire } from 'node:module'
import { mkdtempSync, mkdirSync, realpathSync, rmSync, writeFileSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, dirname, join } from 'node:path'
import { createHash } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import {
  WIN_ASAR_INTEGRITY_FILE, WIN_ASAR_INTEGRITY_ALG, INTEGRITY_RESOURCE_TYPE, INTEGRITY_RESOURCE_ID,
  asarHeaderSha256, integrityPayloadBytes, parseIntegrityPayload, findIntegrityResources,
  uniqueIntegrityResource, assertUnsignedPe, readWinAsarIntegrity, verifyWinAsarIntegrity,
  updateWinAsarIntegrity,
} from './windows-portable-integrity.mjs'

const DIGEST_A = createHash('sha256').update('asar-header-a').digest('hex')
const DIGEST_B = createHash('sha256').update('asar-header-b').digest('hex')

function owned() {
  const parent = realpathSync(tmpdir())
  const root = realpathSync(mkdtempSync(join(parent, 'dsh-win-integrity-')))
  return {
    root,
    clean() {
      const actual = realpathSync(root)
      assert.equal(actual, root)
      assert.equal(dirname(actual), parent)
      assert.ok(basename(actual).startsWith('dsh-win-integrity-'))
      rmSync(actual, { recursive: true, force: true })
    },
  }
}

function payload(overrides = {}) {
  return Buffer.from(JSON.stringify([{ file: WIN_ASAR_INTEGRITY_FILE, alg: WIN_ASAR_INTEGRITY_ALG, value: DIGEST_A, ...overrides }]))
}

function peHeader({ pe32Plus = true, certificateOffset = 0, certificateSize = 0, directories = 16 } = {}) {
  const peOffset = 0x80
  const bytes = Buffer.alloc(0x200)
  bytes.write('MZ', 0, 2, 'ascii')
  bytes.writeUInt32LE(peOffset, 60)
  bytes.write('PE\0\0', peOffset, 4, 'ascii')
  const optional = peOffset + 24
  bytes.writeUInt16LE(pe32Plus ? 0x20B : 0x10B, optional)
  const numberOfRvaAndSizesOffset = pe32Plus ? optional + 108 : optional + 92
  bytes.writeUInt32LE(directories, numberOfRvaAndSizesOffset)
  if (directories >= 5) {
    const dataDirectoryOffset = pe32Plus ? optional + 112 : optional + 96
    bytes.writeUInt32LE(certificateOffset, dataDirectoryOffset + 32)
    bytes.writeUInt32LE(certificateSize, dataDirectoryOffset + 36)
  }
  return bytes
}

function tryLoadResedit() {
  const candidates = [
    process.env.DSH_INTEGRITY_TEST_RESEDIT,
    fileURLToPath(new URL('../.compat-stage/rc3/node_modules/.pnpm/node_modules/resedit', import.meta.url)),
  ]
  for (const candidate of candidates) {
    if (!candidate) continue
    const manifest = join(candidate, 'package.json')
    if (!existsSync(manifest)) continue
    try { return createRequire(manifest)(candidate) } catch { /* next candidate */ }
  }
  return undefined
}

test('header digest matches SHA-256 of headerString bytes and rejects empty input', () => {
  assert.equal(asarHeaderSha256('{"files":{}}'), createHash('sha256').update(Buffer.from('{"files":{}}')).digest('hex'))
  assert.throws(() => asarHeaderSha256(''), /headerString/)
  assert.throws(() => asarHeaderSha256(), /headerString/)
})

test('canonical payload is the electron-builder JSON list with resources\\\\app.asar and SHA256', () => {
  const bytes = integrityPayloadBytes(DIGEST_A)
  assert.equal(bytes.toString('utf8'), JSON.stringify([{ file: 'resources\\app.asar', alg: 'SHA256', value: DIGEST_A }]))
  assert.deepEqual(parseIntegrityPayload(bytes), { file: WIN_ASAR_INTEGRITY_FILE, alg: WIN_ASAR_INTEGRITY_ALG, value: DIGEST_A })
  assert.throws(() => integrityPayloadBytes('ABC'), /digest/)
  assert.throws(() => integrityPayloadBytes(DIGEST_A.toUpperCase()), /digest/)
})

test('integrity JSON missing, duplicate, extra, wrong file or algorithm fail closed', () => {
  assert.throws(() => parseIntegrityPayload(Buffer.from('')), /empty/)
  assert.throws(() => parseIntegrityPayload(Buffer.from('not-json')), /not JSON/)
  assert.throws(() => parseIntegrityPayload(Buffer.from('{}')), /exactly one/)
  assert.throws(() => parseIntegrityPayload(Buffer.from('[]')), /exactly one/)
  assert.throws(() => parseIntegrityPayload(Buffer.from(JSON.stringify([
    { file: WIN_ASAR_INTEGRITY_FILE, alg: WIN_ASAR_INTEGRITY_ALG, value: DIGEST_A },
    { file: WIN_ASAR_INTEGRITY_FILE, alg: WIN_ASAR_INTEGRITY_ALG, value: DIGEST_B },
  ]))), /exactly one/)
  assert.throws(() => parseIntegrityPayload(Buffer.from('null')), /exactly one/)
  assert.throws(() => parseIntegrityPayload(Buffer.from('[null]')), /invalid/)
  assert.throws(() => parseIntegrityPayload(payload({ extra: true })), /unexpected fields/)
  assert.throws(() => parseIntegrityPayload(payload({ file: 'resources/app.asar' })), /file/)
  assert.throws(() => parseIntegrityPayload(payload({ file: 'app.asar' })), /file/)
  assert.throws(() => parseIntegrityPayload(payload({ alg: 'SHA1' })), /algorithm/)
  assert.throws(() => parseIntegrityPayload(payload({ alg: 'sha256' })), /algorithm/)
  assert.throws(() => parseIntegrityPayload(payload({ value: 'deadbeef' })), /digest/)
  assert.throws(() => parseIntegrityPayload(payload({ value: DIGEST_A.toUpperCase() })), /digest/)
})

test('unique INTEGRITY/ELECTRONASAR resource is required; missing and duplicate fail closed', () => {
  const bin = integrityPayloadBytes(DIGEST_A)
  const ok = { type: INTEGRITY_RESOURCE_TYPE, id: INTEGRITY_RESOURCE_ID, bin, lang: 1033, codepage: 1200 }
  assert.equal(uniqueIntegrityResource([ok]), ok)
  assert.throws(() => uniqueIntegrityResource([]), /missing/)
  assert.throws(() => uniqueIntegrityResource([{ type: 'VERSION', id: 1, bin }]), /missing/)
  assert.throws(() => uniqueIntegrityResource([{ type: 'integrity', id: INTEGRITY_RESOURCE_ID, bin }]), /missing/)
  assert.throws(() => uniqueIntegrityResource([ok, { ...ok, lang: 0 }]), /duplicate/)
  assert.deepEqual(findIntegrityResources([ok, { type: 'RT_VERSION', id: 1 }]), [ok])
  assert.throws(() => uniqueIntegrityResource([{ ...ok, bin: payload({ file: 'other' }) }]), /file/)
})

test('PE certificate table presence is treated as signed and rejected', () => {
  assertUnsignedPe(peHeader())
  assertUnsignedPe(peHeader({ pe32Plus: false }))
  assertUnsignedPe(peHeader({ directories: 4 }))
  assert.throws(() => assertUnsignedPe(Buffer.from('not-mz')), /PE image/)
  assert.throws(() => assertUnsignedPe(peHeader({ certificateOffset: 0x200, certificateSize: 0x80 })), /signed/)
  assert.throws(() => assertUnsignedPe(peHeader({ pe32Plus: false, certificateOffset: 1, certificateSize: 1 })), /signed/)
  const truncated = peHeader()
  truncated.writeUInt32LE(0x10, 60)
  assert.throws(() => assertUnsignedPe(truncated), /PE image/)
})

test('synthetic resedit PE fixture can replace, no-op and refuse a signed image', { skip: !tryLoadResedit() && 'locked resedit is not available for a synthetic PE fixture' }, () => {
  const resedit = tryLoadResedit()
  const fixture = owned()
  try {
    const exe = resedit.NtExecutable.createEmpty(false, false)
    const resource = resedit.NtExecutableResource.from(exe)
    resource.entries.push({
      type: INTEGRITY_RESOURCE_TYPE,
      id: INTEGRITY_RESOURCE_ID,
      bin: Uint8Array.from(integrityPayloadBytes(DIGEST_A)).buffer,
      lang: 1033,
      codepage: 1200,
    })
    resource.outputResource(exe)
    const path = join(fixture.root, 'DeepSeek Harness.exe')
    writeFileSync(path, Buffer.from(exe.generate()))
    assert.deepEqual(readWinAsarIntegrity({ executablePath: path, resedit }), {
      file: WIN_ASAR_INTEGRITY_FILE, alg: WIN_ASAR_INTEGRITY_ALG, value: DIGEST_A,
    })
    assert.deepEqual(updateWinAsarIntegrity({ executablePath: path, expectedValue: DIGEST_A, resedit }), {
      updated: false, value: DIGEST_A,
    })
    assert.deepEqual(updateWinAsarIntegrity({ executablePath: path, expectedValue: DIGEST_B, resedit }), {
      updated: true, value: DIGEST_B,
    })
    verifyWinAsarIntegrity({ executablePath: path, expectedValue: DIGEST_B, resedit })
    assert.equal(findIntegrityResources(resedit.NtExecutableResource.from(resedit.NtExecutable.from(readFileSync(path))).entries).length, 1)
    const signed = Buffer.from(readFileSync(path))
    const peOffset = signed.readUInt32LE(60)
    const magic = signed.readUInt16LE(peOffset + 24)
    const dataDirectoryOffset = magic === 0x10B ? peOffset + 24 + 96 : peOffset + 24 + 112
    signed.writeUInt32LE(0x100, dataDirectoryOffset + 32)
    signed.writeUInt32LE(0x40, dataDirectoryOffset + 36)
    const signedPath = join(fixture.root, 'signed.exe')
    writeFileSync(signedPath, signed)
    assert.throws(() => updateWinAsarIntegrity({ executablePath: signedPath, expectedValue: DIGEST_A, resedit }), /signed/)
    const duplicate = resedit.NtExecutable.createEmpty(false, false)
    const duplicateResource = resedit.NtExecutableResource.from(duplicate)
    const listed = { type: INTEGRITY_RESOURCE_TYPE, id: INTEGRITY_RESOURCE_ID, bin: Uint8Array.from(integrityPayloadBytes(DIGEST_A)).buffer, lang: 1033, codepage: 1200 }
    duplicateResource.entries.push(listed, { ...listed, lang: 0 })
    duplicateResource.outputResource(duplicate)
    const duplicatePath = join(fixture.root, 'duplicate.exe')
    writeFileSync(duplicatePath, Buffer.from(duplicate.generate()))
    assert.throws(() => readWinAsarIntegrity({ executablePath: duplicatePath, resedit }), /duplicate/)
    const empty = resedit.NtExecutable.createEmpty(false, false)
    const emptyPath = join(fixture.root, 'empty.exe')
    writeFileSync(emptyPath, Buffer.from(empty.generate()))
    assert.throws(() => readWinAsarIntegrity({ executablePath: emptyPath, resedit }), /missing/)
  } finally { fixture.clean() }
})

test('owned temporary trees are the only fixtures; this file does not run a real Windows Electron package', () => {
  const fixture = owned()
  try {
    mkdirSync(join(fixture.root, 'resources'), { recursive: true })
    writeFileSync(join(fixture.root, 'resources', 'app.asar'), 'fixture-asar')
    assert.equal(basename(fixture.root).startsWith('dsh-win-integrity-'), true)
  } finally { fixture.clean() }
})
