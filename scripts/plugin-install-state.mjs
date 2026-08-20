import { readFile, rename, rm, writeFile } from 'node:fs/promises'

const SHA256 = /^[a-f0-9]{64}$/

export async function markerMatches(path, version, sha256) {
  if (typeof version !== 'string' || version.length === 0 || !SHA256.test(sha256)) return false
  try {
    const marker = JSON.parse(await readFile(path, 'utf8'))
    return marker?.version === version && marker?.sha256 === sha256
  } catch {
    return false
  }
}

export async function writeInstallMarker(path, version, sha256) {
  if (typeof version !== 'string' || version.length === 0 || !SHA256.test(sha256)) {
    throw new Error('Invalid desktop plugin install marker')
  }
  const temporary = `${path}.tmp-${process.pid}`
  try {
    await writeFile(temporary, `${JSON.stringify({ version, sha256 }, null, 2)}\n`, 'utf8')
    await rename(temporary, path)
  } catch (cause) {
    await rm(temporary, { force: true }).catch(() => undefined)
    throw cause
  }
}
