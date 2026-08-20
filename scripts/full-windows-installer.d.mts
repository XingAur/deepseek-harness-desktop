export interface FullTauriConfig {
  bundle: {
    resources: Record<string, string>
    windows: {
      nsis: {
        installerHooks: string
      }
    }
  }
}

export function createFullTauriConfig(rootDirectory: string): FullTauriConfig

export interface VerifyBundledRuntimeInput {
  manifestPath: string
  archivePath: string
  publicKey: string
}

export interface RuntimeManifestSummary {
  target: string
  archive: string
  version: string
  [key: string]: unknown
}

export function verifyBundledRuntime(input: VerifyBundledRuntimeInput): RuntimeManifestSummary
export function fullInstallerName(version: string): string
export function tauriBuildInvocation(
  rootDirectory: string,
  generatedConfig: string,
): { command: string; args: string[] }
export function withPreservedOnlineInstaller(
  paths: { onlinePath: string; fullPath: string },
  build: () => void | Promise<void>,
): Promise<string>

export interface BuildFullWindowsInstallerInput {
  rootDirectory?: string
  environment?: NodeJS.ProcessEnv
  run?: typeof import('node:child_process').spawnSync
}

export function buildFullWindowsInstaller(
  input?: BuildFullWindowsInstallerInput,
): Promise<string>
