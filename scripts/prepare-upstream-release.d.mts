export interface PrepareUpstreamReleaseResult {
  action: 'noop' | 'upgrade'
  previousDshVersion: string
  dshVersion: string
  desktopVersion: string
  runtimeVersion: string
  tag: string
}

export function compareSemVer(left: string, right: string): number
export function fetchLatestDshVersion(fetcher?: typeof fetch): Promise<string>
export function prepareUpstreamRelease(options: {
  root?: string
  latestVersion: string
}): Promise<PrepareUpstreamReleaseResult>
