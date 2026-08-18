import { createPrivateKey, sign } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import { canonicalJson } from './canonical-json.mjs'

export function signDocument(inputPath, outputPath, privateKeyBase64Url, publicKeyBase64Url) {
  if (!privateKeyBase64Url || !publicKeyBase64Url) throw new Error('签名需要显式私钥与公钥，脚本不会生成或保存生产私钥')
  const value = JSON.parse(readFileSync(inputPath, 'utf8'))
  delete value.signature
  const key = createPrivateKey({ key: { kty: 'OKP', crv: 'Ed25519', x: publicKeyBase64Url, d: privateKeyBase64Url }, format: 'jwk' })
  value.signature = sign(null, Buffer.from(canonicalJson(value)), key).toString('base64url')
  writeFileSync(outputPath, `${JSON.stringify(value, null, 2)}\n`, 'utf8')
}

if (process.argv[1] && new URL(import.meta.url).pathname.replace(/^\/[A-Z]:/i, (match) => match.slice(1)) === process.argv[1].replace(/\\/g, '/')) {
  const [, , input, output] = process.argv
  if (!input || !output) throw new Error('Usage: node sign-document.mjs <input.json> <output.json>')
  signDocument(input, output, process.env.DSH_DESKTOP_SIGNING_PRIVATE_KEY, process.env.DSH_DESKTOP_SIGNING_PUBLIC_KEY)
}
