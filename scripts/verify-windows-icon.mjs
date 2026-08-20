import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

export const REQUIRED_WINDOWS_ICON_SIZES = [16, 24, 32, 48, 64, 256]

export function readIcoSizes(path = resolve('src-tauri/icons/icon.ico')) {
  const bytes = readFileSync(path)
  if (bytes.length < 6 || bytes.readUInt16LE(0) !== 0 || bytes.readUInt16LE(2) !== 1) {
    throw new Error('Windows icon is not a valid ICO file')
  }

  const count = bytes.readUInt16LE(4)
  if (count === 0 || bytes.length < 6 + count * 16) {
    throw new Error('Windows icon directory is truncated')
  }

  const sizes = []
  for (let index = 0; index < count; index += 1) {
    const entry = 6 + index * 16
    const width = bytes[entry] === 0 ? 256 : bytes[entry]
    const height = bytes[entry + 1] === 0 ? 256 : bytes[entry + 1]
    const imageSize = bytes.readUInt32LE(entry + 8)
    const imageOffset = bytes.readUInt32LE(entry + 12)
    if (width !== height) throw new Error(`Windows icon frame is not square: ${width}x${height}`)
    if (imageSize === 0 || imageOffset < 6 + count * 16 || imageOffset + imageSize > bytes.length) {
      throw new Error(`Windows icon frame ${width} is corrupt`)
    }
    sizes.push(width)
  }

  if (new Set(sizes).size !== sizes.length) {
    throw new Error('Windows icon contains duplicate frame sizes')
  }
  return sizes.sort((left, right) => left - right)
}

export function verifyWindowsIcon(
  configPath = resolve('src-tauri/tauri.windows.conf.json'),
  iconPath = resolve('src-tauri/icons/icon.ico'),
) {
  const config = JSON.parse(readFileSync(configPath, 'utf8'))
  const nsis = config?.bundle?.windows?.nsis
  if (nsis?.installerIcon !== 'icons/icon.ico' || nsis?.uninstallerIcon !== 'icons/icon.ico') {
    throw new Error('Installer and uninstaller must use icons/icon.ico')
  }
  const sizes = readIcoSizes(iconPath)
  for (const required of REQUIRED_WINDOWS_ICON_SIZES) {
    if (!sizes.includes(required)) throw new Error(`Windows icon is missing the ${required}px frame`)
  }
  return sizes
}

if (process.argv[1]?.replaceAll('\\', '/').endsWith('/scripts/verify-windows-icon.mjs')) {
  const sizes = verifyWindowsIcon()
  console.log(`Windows icon verified: ${sizes.join(', ')}px`)
}
