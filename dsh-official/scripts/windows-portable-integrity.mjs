#!/usr/bin/env node
/** Parse, verify and replace the unique Electron INTEGRITY/ELECTRONASAR resource. */
import { createHash, randomUUID } from 'node:crypto'
import { closeSync, fsyncSync, lstatSync, openSync, readFileSync, realpathSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'

export const WIN_ASAR_INTEGRITY_FILE = 'resources\\app.asar'
export const WIN_ASAR_INTEGRITY_ALG = 'SHA256'
export const INTEGRITY_RESOURCE_TYPE = 'INTEGRITY'
export const INTEGRITY_RESOURCE_ID = 'ELECTRONASAR'

const PE32_MAGIC = 0x10B
const PE32_PLUS_MAGIC = 0x20B
const DIGEST = /^[0-9a-f]{64}$/u

function pathExists(path) {
  try { lstatSync(path); return true } catch (error) {
    if (error.code === 'ENOENT') return false
    throw error
  }
}

function contained(root, path) {
  const subpath = relative(root, path)
  return subpath === '' || (!isAbsolute(subpath) && subpath !== '..' && !subpath.startsWith(`..${sep}`))
}

function asBuffer(bin) {
  if (Buffer.isBuffer(bin)) return bin
  if (bin instanceof ArrayBuffer) return Buffer.from(bin)
  if (ArrayBuffer.isView(bin)) return Buffer.from(bin.buffer, bin.byteOffset, bin.byteLength)
  throw new Error('INTEGRITY resource is not binary')
}

/**
 * SHA-256 of the final ASAR headerString, matching Electron's Windows integrity digest.
 * @param {string} headerString Raw ASAR header JSON.
 * @returns {string} Lowercase hex digest.
 */
export function asarHeaderSha256(headerString) {
  if (typeof headerString !== 'string' || headerString.length === 0) throw new Error('ASAR headerString is missing')
  return createHash('sha256').update(Buffer.from(headerString)).digest('hex')
}

/**
 * Canonical INTEGRITY payload bytes used by app-builder-lib@26.17.0.
 * @param {string} value Lowercase SHA-256 of the ASAR headerString.
 * @returns {Buffer} UTF-8 JSON payload.
 */
export function integrityPayloadBytes(value) {
  if (typeof value !== 'string' || !DIGEST.test(value)) throw new Error('invalid ASAR integrity digest')
  return Buffer.from(JSON.stringify([{ file: WIN_ASAR_INTEGRITY_FILE, alg: WIN_ASAR_INTEGRITY_ALG, value }]))
}

/**
 * Parse the unique Electron ASAR integrity list. Missing, duplicate, extra or mistyped fields fail closed.
 * @param {Buffer | ArrayBuffer | ArrayBufferView} bin Resource bytes.
 * @returns {{ file: string, alg: string, value: string }}
 */
export function parseIntegrityPayload(bin) {
  const bytes = asBuffer(bin)
  if (bytes.length === 0) throw new Error('INTEGRITY resource is empty')
  let parsed
  try { parsed = JSON.parse(bytes.toString('utf8')) } catch {
    throw new Error('INTEGRITY resource is not JSON')
  }
  if (!Array.isArray(parsed) || parsed.length !== 1) throw new Error('INTEGRITY resource must contain exactly one ASAR entry')
  const entry = parsed[0]
  if (entry === null || typeof entry !== 'object' || Array.isArray(entry)) throw new Error('INTEGRITY resource entry is invalid')
  const { file, alg, value, ...rest } = entry
  if (Object.keys(rest).length !== 0) throw new Error('INTEGRITY resource has unexpected fields')
  if (file !== WIN_ASAR_INTEGRITY_FILE) throw new Error(`unexpected INTEGRITY file: ${JSON.stringify(file)}`)
  if (alg !== WIN_ASAR_INTEGRITY_ALG) throw new Error(`unexpected INTEGRITY algorithm: ${JSON.stringify(alg)}`)
  if (typeof value !== 'string' || !DIGEST.test(value)) throw new Error('invalid INTEGRITY digest')
  return { file, alg, value }
}

/**
 * Select INTEGRITY/ELECTRONASAR entries. Type and id must match Electron's string names exactly.
 * @param {readonly { type?: unknown, id?: unknown }[]} entries PE resource entries.
 * @returns {typeof entries}
 */
export function findIntegrityResources(entries) {
  if (!Array.isArray(entries)) throw new Error('PE resource entries are missing')
  return entries.filter(entry => entry !== null && typeof entry === 'object'
    && entry.type === INTEGRITY_RESOURCE_TYPE && entry.id === INTEGRITY_RESOURCE_ID)
}

/**
 * Reject missing or duplicated INTEGRITY/ELECTRONASAR resources before any write.
 * @param {readonly { type?: unknown, id?: unknown, bin?: unknown }[]} entries PE resource entries.
 * @returns {{ type: unknown, id: unknown, bin: unknown, lang?: unknown, codepage?: unknown }}
 */
export function uniqueIntegrityResource(entries) {
  const matches = findIntegrityResources(entries)
  if (matches.length === 0) throw new Error('missing INTEGRITY/ELECTRONASAR resource')
  if (matches.length !== 1) throw new Error('duplicate INTEGRITY/ELECTRONASAR resource')
  parseIntegrityPayload(matches[0].bin)
  return matches[0]
}

/**
 * Fail closed when the PE certificate table is present. This unsigned line never rewrites a signed EXE.
 * @param {Buffer} bytes Complete executable.
 */
export function assertUnsignedPe(bytes) {
  if (!Buffer.isBuffer(bytes) || bytes.length < 64 || bytes.subarray(0, 2).toString('ascii') !== 'MZ') {
    throw new Error('Electron executable is not a PE image')
  }
  const peOffset = bytes.readUInt32LE(60)
  if (peOffset < 64 || peOffset + 26 > bytes.length || bytes.subarray(peOffset, peOffset + 4).toString('ascii') !== 'PE\0\0') {
    throw new Error('Electron executable is not a PE image')
  }
  const optionalHeaderOffset = peOffset + 24
  const magic = bytes.readUInt16LE(optionalHeaderOffset)
  const numberOfRvaAndSizesOffset = magic === PE32_MAGIC ? optionalHeaderOffset + 92
    : magic === PE32_PLUS_MAGIC ? optionalHeaderOffset + 108 : undefined
  if (numberOfRvaAndSizesOffset === undefined) throw new Error('unsupported PE optional header')
  if (numberOfRvaAndSizesOffset + 4 > bytes.length) throw new Error('truncated PE optional header')
  const directories = bytes.readUInt32LE(numberOfRvaAndSizesOffset)
  if (directories < 5) return
  const dataDirectoryOffset = magic === PE32_MAGIC ? optionalHeaderOffset + 96 : optionalHeaderOffset + 112
  const certificateDirectoryOffset = dataDirectoryOffset + (4 * 8)
  if (certificateDirectoryOffset + 8 > bytes.length) throw new Error('truncated PE certificate table')
  const certificateOffset = bytes.readUInt32LE(certificateDirectoryOffset)
  const certificateSize = bytes.readUInt32LE(certificateDirectoryOffset + 4)
  if (certificateOffset !== 0 || certificateSize !== 0) {
    throw new Error('signed Electron executable is not allowed on this unsigned release line')
  }
}

/** Load the stage-locked resedit. Never fall back to a global install. */
export function loadResedit(stageRoot) {
  const stage = realpathSync(stageRoot)
  const require = createRequire(join(stage, 'package.json'))
  const candidates = [join(stage, 'node_modules/.pnpm/node_modules/resedit'), 'resedit']
  const errors = []
  for (const candidate of candidates) {
    try { return require(candidate) } catch (error) {
      errors.push(`${candidate}: ${error instanceof Error ? error.message : String(error)}`)
    }
  }
  throw new Error(`unable to load locked resedit from stage: ${errors.join('; ')}`)
}

function parseExecutableResources(bytes, resedit) {
  assertUnsignedPe(bytes)
  let executable
  try {
    executable = resedit.NtExecutable.from(bytes)
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    if (/signed/iu.test(message)) throw new Error('signed Electron executable is not allowed on this unsigned release line')
    throw error
  }
  const resource = resedit.NtExecutableResource.from(executable)
  const entry = uniqueIntegrityResource(resource.entries)
  return { executable, resource, entry, payload: parseIntegrityPayload(entry.bin) }
}

function verifyParsedIntegrity(path, expectedValue, resedit) {
  if (typeof expectedValue !== 'string' || !DIGEST.test(expectedValue)) throw new Error('invalid ASAR integrity digest')
  const stat = lstatSync(path)
  if (!stat.isFile() || stat.isSymbolicLink()) throw new Error('Electron executable must be a regular file')
  const parsed = parseExecutableResources(readFileSync(path), resedit)
  if (parsed.payload.value !== expectedValue) {
    throw new Error(`INTEGRITY digest ${parsed.payload.value} does not match ASAR header ${expectedValue}`)
  }
  return parsed
}

/**
 * Read the unique INTEGRITY/ELECTRONASAR payload after rejecting a signed EXE.
 * @param {{ executablePath: string, resedit?: object, stageRoot?: string }} options Executable and locked resedit.
 * @returns {{ file: string, alg: string, value: string }}
 */
export function readWinAsarIntegrity({ executablePath, resedit, stageRoot }) {
  const loaded = resedit ?? loadResedit(stageRoot)
  const path = realpathSync(executablePath)
  const parsed = parseExecutableResources(readFileSync(path), loaded)
  return parsed.payload
}

/**
 * Confirm the unique INTEGRITY resource matches the expected ASAR header digest.
 * @param {{ executablePath: string, expectedValue: string, resedit?: object, stageRoot?: string }} options
 * @returns {{ file: string, alg: string, value: string }}
 */
export function verifyWinAsarIntegrity({ executablePath, expectedValue, resedit, stageRoot }) {
  const loaded = resedit ?? loadResedit(stageRoot)
  return verifyParsedIntegrity(realpathSync(executablePath), expectedValue, loaded).payload
}

/**
 * Replace the existing unique INTEGRITY/ELECTRONASAR resource. Never calls app-builder-lib addWinAsarIntegrity.
 * Same digest is a no-op. Writes a temporary EXE, verifies it, then atomically replaces the original.
 * @param {{ executablePath: string, expectedValue: string, resedit?: object, stageRoot?: string }} options
 * @returns {{ updated: boolean, value: string }}
 */
export function updateWinAsarIntegrity({ executablePath, expectedValue, resedit, stageRoot }) {
  if (typeof expectedValue !== 'string' || !DIGEST.test(expectedValue)) throw new Error('invalid ASAR integrity digest')
  const loaded = resedit ?? loadResedit(stageRoot)
  const path = realpathSync(executablePath)
  if (/^\/Applications(?:\/|$)/iu.test(path)) throw new Error('refusing to rewrite an installed application executable')
  const current = parseExecutableResources(readFileSync(path), loaded)
  if (current.payload.value === expectedValue) return { updated: false, value: expectedValue }
  current.entry.bin = Uint8Array.from(integrityPayloadBytes(expectedValue)).buffer
  current.resource.outputResource(current.executable)
  const generated = Buffer.from(current.executable.generate())
  assertUnsignedPe(generated)
  const directory = realpathSync(dirname(path))
  if (/^\/Applications(?:\/|$)/iu.test(directory) || !contained(directory, path)) {
    throw new Error('Electron executable rewrite must stay beside the original file')
  }
  const temporary = join(directory, `.${basename(path)}.${randomUUID()}.tmp`)
  let descriptor
  try {
    descriptor = openSync(temporary, 'wx', lstatSync(path).mode & 0o777)
    writeFileSync(descriptor, generated)
    fsyncSync(descriptor)
    closeSync(descriptor)
    descriptor = undefined
    verifyParsedIntegrity(temporary, expectedValue, loaded)
    renameSync(temporary, path)
  } finally {
    if (descriptor !== undefined) closeSync(descriptor)
    if (pathExists(temporary)) rmSync(temporary)
  }
  verifyParsedIntegrity(path, expectedValue, loaded)
  return { updated: true, value: expectedValue }
}

/**
 * Hash an ASAR archive headerString using the same @electron/asar getRawHeader used by finalize.
 * @param {{ asar: { getRawHeader: (path: string) => { headerString: string }, uncache?: (path: string) => void }, archivePath: string }} options
 * @returns {string}
 */
export function asarArchiveHeaderSha256({ asar, archivePath }) {
  if (typeof asar?.uncache === 'function') asar.uncache(archivePath)
  return asarHeaderSha256(asar.getRawHeader(archivePath).headerString)
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const [stageRoot, executablePath, archivePath, mode = 'update', ...extra] = process.argv.slice(2)
  if (!stageRoot || !executablePath || !archivePath || extra.length || !['update', 'verify'].includes(mode)) {
    throw new Error('usage: windows-portable-integrity.mjs <stageRoot> <DeepSeek Harness.exe> <app.asar> [update|verify]')
  }
  const require = createRequire(join(realpathSync(stageRoot), 'package.json'))
  const asar = require(join(realpathSync(stageRoot), 'node_modules/.pnpm/node_modules/@electron/asar'))
  const expectedValue = asarArchiveHeaderSha256({ asar, archivePath: realpathSync(archivePath) })
  const resedit = loadResedit(stageRoot)
  const result = mode === 'verify'
    ? { updated: false, ...verifyWinAsarIntegrity({ executablePath, expectedValue, resedit, stageRoot }) }
    : updateWinAsarIntegrity({ executablePath, expectedValue, resedit, stageRoot })
  console.log(`windows asar integrity ${result.updated ? 'updated' : 'verified'}: ${expectedValue}`)
}
