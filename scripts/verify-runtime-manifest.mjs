import { createHash, createPublicKey, verify } from 'node:crypto'
import { basename, resolve } from 'node:path'
import { readFileSync } from 'node:fs'

import { canonicalJson } from './canonical-json.mjs'

export function verifyRuntimeManifest({
  manifestPath,
  archivePath,
  target,
  publicKey,
  version,
}) {
  if (!publicKey) throw new Error('DSH_DESKTOP_RELEASE_PUBLIC_KEY is required')
  const manifest = JSON.parse(readFileSync(resolve(manifestPath), 'utf8'))
  if (manifest.target !== target) {
    throw new Error(`Runtime target 不匹配：${manifest.target}/${target}`)
  }
  if (version && manifest.version !== version) {
    throw new Error(`Runtime version 不匹配：${manifest.version}/${version}`)
  }
  if (typeof manifest.url !== 'string' || !manifest.url.startsWith('https://')) {
    throw new Error('Runtime URL 必须使用 HTTPS')
  }
  if (version && !manifest.url.includes(`/releases/download/runtime-v${version}/`)) {
    throw new Error(`Runtime URL 必须指向 runtime-v${version} Release`)
  }
  if (typeof manifest.signature !== 'string' || !manifest.signature) {
    throw new Error('Runtime 清单缺少签名')
  }
  const key = createPublicKey({
    key: { kty: 'OKP', crv: 'Ed25519', x: publicKey },
    format: 'jwk',
  })
  const signature = Buffer.from(manifest.signature, 'base64url')
  if (!verify(null, Buffer.from(canonicalJson(manifest, 'signature')), key, signature)) {
    throw new Error('Runtime 清单签名校验失败')
  }
  const archive = readFileSync(resolve(archivePath))
  if (archive.length !== manifest.size) {
    throw new Error(`Runtime 归档大小不匹配：${archive.length}/${manifest.size}`)
  }
  const sha256 = createHash('sha256').update(archive).digest('hex')
  if (sha256.toLowerCase() !== String(manifest.sha256).toLowerCase()) {
    throw new Error('Runtime 归档 SHA-256 不匹配')
  }
  if (!manifest.url.endsWith(`/${basename(archivePath)}`)) {
    throw new Error('Runtime URL 与归档文件名不匹配')
  }
  return manifest
}

if (process.argv[1] && new URL(import.meta.url).pathname.replace(/^\/[A-Z]:/i, (match) => match.slice(1)) === process.argv[1].replace(/\\/g, '/')) {
  const [, , manifestPath, archivePath, target, version] = process.argv
  if (!manifestPath || !archivePath || !target) {
    throw new Error('Usage: node scripts/verify-runtime-manifest.mjs <manifest.json> <archive> <target> [version]')
  }
  verifyRuntimeManifest({
    manifestPath,
    archivePath,
    target,
    version,
    publicKey: process.env.DSH_DESKTOP_RELEASE_PUBLIC_KEY,
  })
  console.log(`Verified Runtime manifest: ${target}`)
}
