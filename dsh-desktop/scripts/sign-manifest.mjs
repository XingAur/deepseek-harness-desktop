import { signDocument } from './sign-document.mjs'

const [, , input, output = input] = process.argv
if (!input) throw new Error('Usage: node sign-manifest.mjs <input.json> [output.json]')
signDocument(input, output, process.env.DSH_DESKTOP_SIGNING_PRIVATE_KEY, process.env.DSH_DESKTOP_SIGNING_PUBLIC_KEY)
