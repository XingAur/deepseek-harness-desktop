export interface UpdaterReleaseConfig {
  bundle: {
    createUpdaterArtifacts: boolean
    resources: { '../runtime/': 'runtime/' }
  }
  plugins?: {
    updater: {
      pubkey: string
      endpoints: string[]
      windows: { installMode: 'passive' }
    }
  }
}

export const productionUpdaterEndpoint: string
export function updaterConfig(options: {
  platform: 'windows-x86_64' | 'darwin-aarch64'
  publicKey?: string
  endpoint?: string
}): UpdaterReleaseConfig
