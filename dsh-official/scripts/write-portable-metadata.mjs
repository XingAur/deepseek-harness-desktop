#!/usr/bin/env node
import { createHash } from 'node:crypto'
import { createReadStream, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs'
import { basename, join, relative, resolve, sep } from 'node:path'

const [artifactArgument, overlayArgument, officialCommit, officialVersion, buildVersion, platform, arch, signing] = process.argv.slice(2)
if (!artifactArgument || !overlayArgument || !officialCommit || !officialVersion || !buildVersion || !platform || !arch || !signing) {
  throw new Error('usage: write-portable-metadata.mjs <artifact> <overlay> <official-commit> <official-version> <build-version> <platform> <arch> <signing>')
}
const artifact = resolve(artifactArgument)
const overlay = resolve(overlayArgument)

function filesBelow(directory) {
  const files = []
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name)
    if (entry.isDirectory()) files.push(...filesBelow(path))
    else if (entry.isFile()) files.push(path)
  }
  return files
}
function sha256(value) { return createHash('sha256').update(value).digest('hex') }
async function sha256File(path) {
  const digest = createHash('sha256')
  for await (const chunk of createReadStream(path)) digest.update(chunk)
  return digest.digest('hex')
}

const artifactSha256 = await sha256File(artifact)
const overlayDigest = createHash('sha256')
for (const path of [join(overlay, 'src'), join(overlay, 'public')].flatMap(filesBelow).sort()) {
  const name = relative(overlay, path).split(sep).join('/')
  overlayDigest.update(`${name}\0${sha256(readFileSync(path))}\n`)
}
const overlayHash = overlayDigest.digest('hex')
const checksumPath = `${artifact}.sha256`
const recordPath = `${artifact}.build.json`
writeFileSync(checksumPath, `${artifactSha256}  ${basename(artifact)}\n`)
writeFileSync(recordPath, `${JSON.stringify({
  artifact: basename(artifact),
  size: statSync(artifact).size,
  officialCommit,
  officialVersion,
  buildVersion,
  overlayHash,
  sha256: artifactSha256,
  platform,
  arch,
  signing,
  update: 'manual',
  node: process.version,
}, undefined, 2)}\n`)
process.stdout.write(`${artifact}\n${artifactSha256}\n`)
