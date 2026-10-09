#!/usr/bin/env node
import { lstatSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'

const [treeArgument, ...forbiddenArguments] = process.argv.slice(2)
if (!treeArgument || forbiddenArguments.length === 0) {
  throw new Error('usage: sanitize-portable-paths.mjs <tree> <forbidden-path> [...]')
}
const tree = resolve(treeArgument)
const forbidden = forbiddenArguments.map(value => resolve(value))
const textFile = /\.(?:cjs|css|html|js|json|map|mjs|ts|txt|yaml|yml)$/iu
let changed = 0

function variants(value) {
  return [...new Set([value, value.replaceAll('\\', '/'), value.replaceAll('/', '\\')])]
}
function sanitize(path) {
  const stat = lstatSync(path)
  if (stat.isSymbolicLink()) return
  if (stat.isDirectory()) {
    for (const name of readdirSync(path)) sanitize(join(path, name))
    return
  }
  if (!stat.isFile() || !textFile.test(path)) return
  const source = readFileSync(path, 'utf8')
  let output = source
  for (const root of forbidden) {
    for (const value of variants(root)) output = output.replaceAll(value, '<portable-build-root>')
  }
  if (output !== source) {
    writeFileSync(path, output)
    changed += 1
  }
}

sanitize(tree)
process.stdout.write(`portable path sanitizer changed ${changed} file(s): ${tree}\n`)
