export interface WindowsTauriConfig {
  bundle: {
    resources: Record<string, string>
  }
}

export function createWindowsTauriConfig(rootDirectory: string): WindowsTauriConfig

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
export function windowsInstallerName(version: string): string
export function tauriBuildInvocation(
  rootDirectory: string,
  generatedConfig: string,
): { command: string; args: string[] }
export function replaceReleaseInstaller(
  paths: { generatedPath: string; releasePath: string },
  build: () => void | Promise<void>,
): Promise<string>

export interface BuildWindowsInstallerInput {
  rootDirectory?: string
  environment?: NodeJS.ProcessEnv
  run?: typeof import('node:child_process').spawnSync
}

export function buildWindowsInstaller(
  input?: BuildWindowsInstallerInput,
): Promise<string>
