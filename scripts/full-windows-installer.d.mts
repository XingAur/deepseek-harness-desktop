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
