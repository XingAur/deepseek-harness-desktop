export type RuntimeTarget = 'windows-x86_64' | 'darwin-aarch64'

export interface UnsignedRuntimeManifest {
  schemaVersion: 1
  version: string
  dshVersion: string
  target: RuntimeTarget
  url: string
  size: number
  sha256: string
  desktopPluginSha256?: string
  archive: 'zip' | 'tar-gz'
  entrypoint: 'node.exe' | 'bin/node'
  args: ['app/launcher.mjs', '--port', '{port}']
  healthPath: '/__desktop/health'
  signature: ''
}

export function runtimeReleaseAssetNames(target: RuntimeTarget): { archiveName: string; manifestName: string }
export function createUnsignedRuntimeManifest(options: {
  archivePath: string
  target: RuntimeTarget
  version: string
  url: string
  dshVersion?: string
  desktopPluginSha256?: string
}): UnsignedRuntimeManifest
export function writeUnsignedRuntimeManifest(options: {
  archivePath: string
  target: RuntimeTarget
  version: string
  url: string
  dshVersion?: string
  desktopPluginSha256?: string
  outputPath?: string
}): { outputPath: string; manifest: UnsignedRuntimeManifest }
