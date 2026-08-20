export interface RuntimeSourceConfigInput {
  tag: string
  repository: string
  target?: 'windows-x86_64' | 'darwin-aarch64'
}

export interface RuntimeSourceConfig {
  endpoint: string
  allowedHosts: string[]
}

export function runtimeSourceConfig(input: RuntimeSourceConfigInput): RuntimeSourceConfig
