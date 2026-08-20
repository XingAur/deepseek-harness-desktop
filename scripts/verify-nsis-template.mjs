import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { createHash } from 'node:crypto'

export const EXPECTED_UPSTREAM_SHA256 = '20f4ecc730defb71f1342eaeaec4021df13be3d843abba0effe88ea5835fa079'

export function verifyNsisTemplate(path = resolve('src-tauri/windows/installer.nsi')) {
  const source = readFileSync(path, 'utf8')
  const metadata = {
    tauriCliVersion: metadataValue(source, 'DSH_TAURI_CLI_VERSION'),
    tauriBundlerVersion: metadataValue(source, 'DSH_TAURI_BUNDLER_VERSION'),
    upstreamSha256: metadataValue(source, 'DSH_UPSTREAM_SHA256'),
    requiredMarkers: ['MAINBINARYSRCPATH', 'Section Install', 'WriteUninstaller']
      .filter((marker) => source.includes(marker)),
  }
  if (metadata.tauriCliVersion !== '2.11.4') throw new Error('Unexpected Tauri CLI baseline')
  if (metadata.tauriBundlerVersion !== '2.9.4') throw new Error('Unexpected tauri-bundler baseline')
  if (metadata.upstreamSha256 !== EXPECTED_UPSTREAM_SHA256) throw new Error('Unexpected upstream NSIS hash')
  const strippedHash = createHash('sha256')
    .update(stripCustomBlocks(source).replaceAll('\r\n', '\n'))
    .digest('hex')
  if (strippedHash !== EXPECTED_UPSTREAM_SHA256) {
    throw new Error(`Vendored NSIS baseline drifted outside provisioning blocks: ${strippedHash}`)
  }
  if (metadata.requiredMarkers.length !== 3) throw new Error('Required NSIS shell markers are incomplete')
  for (const forbidden of ['Section ProvisionRuntime', '--provision-runtime', 'CommitProvisioning', 'RollbackProvisioning']) {
    if (source.includes(forbidden)) throw new Error(`Installer must not provision Runtime: ${forbidden}`)
  }
  assertOrder(source, [
    'Section Install',
    'File "${MAINBINARYSRCPATH}"',
    'WriteUninstaller',
    'WriteRegStr SHCTX "${UNINSTKEY}" "DisplayName"',
    'Call CreateOrUpdateStartMenuShortcut',
  ])
  const cleanupIndex = source.indexOf('--cleanup-app-data')
  const cleanupResetIndex = source.indexOf('StrCpy $DeleteAppDataCheckboxState 0', cleanupIndex)
  const binaryDeleteIndex = source.indexOf('Delete "$INSTDIR\\${MAINBINARYNAME}.exe"')
  if (cleanupIndex < 0 || cleanupResetIndex <= cleanupIndex || binaryDeleteIndex <= cleanupResetIndex) {
    throw new Error('Explicit app-data cleanup is missing or ordered after the app binary deletion')
  }
  return metadata
}

function stripCustomBlocks(source) {
  return source
    .replace(
      /^[ \t]*; DSH_PROVISIONING_BEGIN[^\r\n]*\r?\n[\s\S]*?^[ \t]*; DSH_PROVISIONING_END[^\r\n]*\r?\n?/gm,
      '',
    )
    .replace(
      /^[ \t]*; DSH_CUSTOM_BEGIN[^\r\n]*\r?\n[\s\S]*?^[ \t]*; DSH_CUSTOM_END[^\r\n]*\r?\n?/gm,
      '',
    )
}

function metadataValue(source, key) {
  const match = source.match(new RegExp(`^; ${key}=([^\\r\\n]+)$`, 'm'))
  if (!match) throw new Error(`Missing ${key}`)
  return match[1].trim()
}

function assertOrder(source, markers) {
  let previous = -1
  for (const marker of markers) {
    const next = source.indexOf(marker)
    if (next < 0 || next <= previous) throw new Error(`NSIS marker is missing or out of order: ${marker}`)
    previous = next
  }
}

if (process.argv[1]?.replaceAll('\\', '/').endsWith('/scripts/verify-nsis-template.mjs')) {
  const metadata = verifyNsisTemplate()
  console.log(`NSIS template verified: tauri-bundler ${metadata.tauriBundlerVersion}`)
}
