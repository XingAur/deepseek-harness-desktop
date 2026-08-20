import { createPrivateKey, generateKeyPairSync } from 'node:crypto'
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, isAbsolute, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'

export function createRuntimeSigningState(outputPath) {
  const path = absolutePath(outputPath)
  const { privateKey, publicKey } = generateKeyPairSync('ed25519')
  const privateJwk = privateKey.export({ format: 'jwk' })
  const publicJwk = publicKey.export({ format: 'jwk' })
  if (typeof publicJwk.x !== 'string') throw new Error('无法导出 E2E Runtime Ed25519 公钥')
  const state = { schemaVersion: 1, privateJwk, publicKey: publicJwk.x }
  mkdirSync(dirname(path), { recursive: true })
  writeFileSync(path, JSON.stringify(state, null, 2), { encoding: 'utf8', mode: 0o600 })
  return { path, publicKey: state.publicKey }
}

export function loadRuntimeSigningState(inputPath) {
  const path = absolutePath(inputPath)
  const state = JSON.parse(readFileSync(path, 'utf8'))
  if (state?.schemaVersion !== 1 || typeof state.publicKey !== 'string' || state.privateJwk?.kty !== 'OKP') {
    throw new Error('E2E Runtime signing state 格式无效')
  }
  return {
    privateKey: createPrivateKey({ key: state.privateJwk, format: 'jwk' }),
    publicKey: state.publicKey,
  }
}

function absolutePath(value) {
  if (typeof value !== 'string' || value.trim() === '') throw new Error('必须提供 signing state 路径')
  const path = resolve(value)
  if (!isAbsolute(path)) throw new Error('signing state 路径必须是绝对路径')
  return path
}

if (process.argv[1] !== undefined && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const result = createRuntimeSigningState(process.argv[2])
  process.stdout.write(JSON.stringify(result))
}
