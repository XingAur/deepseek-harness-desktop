#!/usr/bin/env node
import { readFile } from 'node:fs/promises'
import { build } from 'esbuild'

const mode = process.argv[2]
const manifest = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'))

// Build identity: packaging scripts pass both values; a plain local bundle falls
// back to official-lock.json and marks the build as a development build.
const envOfficial = process.env.DSH_OVERLAY_OFFICIAL_VERSION?.trim()
const envBuild = process.env.DSH_OVERLAY_BUILD_VERSION?.trim()
let officialVersion = envOfficial === undefined || envOfficial === '' ? undefined : envOfficial
if (officialVersion === undefined) {
  try {
    const lock = JSON.parse(await readFile(new URL('../../official-lock.json', import.meta.url), 'utf8'))
    if (typeof lock.version === 'string' && lock.version !== '') officialVersion = lock.version
  } catch {
    // A staged build has no repository lock; the packaging scripts supply the version.
  }
}
const meta = {
  officialVersion: officialVersion ?? '0.0.0',
  buildVersion: envBuild === undefined || envBuild === '' ? `${officialVersion ?? '0.0.0'}_dev` : envBuild,
}
const define = { __DSH_OVERLAY_META__: JSON.stringify(meta) }

if (mode === 'host') {
  await build({
    entryPoints: ['src/index.ts'],
    bundle: true,
    platform: 'node',
    format: 'esm',
    outfile: 'lib/index.js',
    packages: 'external',
    define,
  })
} else if (mode === 'client') {
  await build({
    entryPoints: ['src/client/index.ts'],
    bundle: true,
    format: 'cjs',
    outfile: 'lib/client.js',
    jsx: 'automatic',
    external: [
      'react', 'react/jsx-runtime', 'react-dom', '@deepseek-ai/cordis',
      '@deepseek-ai/dsh-client-ui-slots',
      '@deepseek-ai/dsh-client-ui-settings-models', '@deepseek-ai/dsh-client-ui-settings-models/client',
      '@deepseek-ai/dsh-client-ui-sidebar', '@deepseek-ai/dsh-client-ui-sidebar/client',
      '@deepseek-ai/dsh-client-ui-settings', '@deepseek-ai/dsh-client-ui-settings/client',
    ],
    banner: { js: `window.__ModuleLoader__.load({id:${JSON.stringify(manifest.name)},factory:(require)=>{var module={exports:{}};var exports=module.exports;` },
    footer: { js: 'return module.exports;}});' },
    define,
  })
} else {
  throw new Error('usage: bundle.mjs <host|client>')
}
