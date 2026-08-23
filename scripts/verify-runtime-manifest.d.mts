export function verifyRuntimeManifest(options: {
  manifestPath: string
  archivePath: string
  target: string
  publicKey: string
  version?: string
}): Record<string, unknown>
