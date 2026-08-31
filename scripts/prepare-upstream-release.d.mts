import type { DshUpstreamSource } from './release-versions.mjs'

export interface PrepareUpstreamReleaseResult {
  action: 'noop' | 'source-update' | 'upgrade'
  previousDshVersion: string
  dshVersion: string
  desktopVersion: string
  runtimeVersion: string
  tag: string
  previousUpstreamTag: string
  upstreamTag: string
  upstreamCommit: string
}

export interface DshSource extends DshUpstreamSource {
  version: string
}

export interface GitTagRunnerResult {
  status: number | null
  stdout?: string
}

export function compareSemVer(left: string, right: string): number
export function fetchLatestDshVersion(fetcher?: typeof fetch): Promise<string>
export function parseDshTagRefs(output: string): DshSource
export function fetchLatestDshSource(
  runner?: (command: string, args: string[], options: Record<string, unknown>) => GitTagRunnerResult,
): DshSource
export function prepareUpstreamRelease(options: {
  root?: string
  latestVersion: string
  latestSource: DshUpstreamSource
}): Promise<PrepareUpstreamReleaseResult>
