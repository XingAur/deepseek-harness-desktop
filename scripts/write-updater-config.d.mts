export interface UpdaterReleaseConfig {
  bundle: { createUpdaterArtifacts: true }
  plugins: {
    updater: {
      pubkey: string
      endpoints: string[]
      windows: { installMode: 'passive' }
    }
  }
}

export const productionUpdaterEndpoint: string
export function updaterConfig(publicKey: string | undefined, endpoint?: string): UpdaterReleaseConfig
