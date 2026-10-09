#!/usr/bin/env node
import { closeSync, lstatSync, openSync, readSync, readdirSync, realpathSync } from 'node:fs'
import { join, resolve } from 'node:path'

const [treeArgument, ...needleArguments] = process.argv.slice(2)
if (!treeArgument || needleArguments.length === 0) throw new Error('usage: verify-portable-paths.mjs <tree> <forbidden-path> [...]')
const tree = resolve(treeArgument)
const forbiddenPaths = [...new Set(needleArguments.map(value => resolve(value)))]
const needleTexts = [...new Set(forbiddenPaths.flatMap(absolute => {
  const variants = [absolute, absolute.replaceAll('\\', '/'), absolute.replaceAll('/', '\\')]
  return variants.flatMap(value => [value, value.toLowerCase(), value.toUpperCase()])
}))]
const needles = needleTexts.flatMap(value => [Buffer.from(value, 'utf8'), Buffer.from(value, 'utf16le')])
const longest = Math.max(...needles.map(value => value.length))
const buffer = Buffer.allocUnsafe(1024 * 1024 + longest)

function inspect(path) {
  const metadata = lstatSync(path)
  if (metadata.isSymbolicLink()) {
    const target = realpathSync(path)
    const normalizedTarget = target.toLowerCase()
    if (forbiddenPaths.some(value => normalizedTarget.startsWith(value.toLowerCase()))) throw new Error(`portable path check: external source link ${path} -> ${target}`)
    return
  }
  if (metadata.isDirectory()) {
    for (const entry of readdirSync(path)) inspect(join(path, entry))
    return
  }
  if (!metadata.isFile()) return
  const descriptor = openSync(path, 'r')
  try {
    let carry = 0
    for (;;) {
      const count = readSync(descriptor, buffer, carry, buffer.length - carry, null)
      if (count === 0) break
      const length = carry + count
      const view = buffer.subarray(0, length)
      if (needles.some(needle => view.includes(needle))) throw new Error(`portable path check: forbidden path in ${path}`)
      carry = Math.min(longest - 1, length)
      view.copy(buffer, 0, length - carry, length)
    }
  } finally { closeSync(descriptor) }
}

inspect(tree)
process.stdout.write(`portable path check passed: ${tree}\n`)
