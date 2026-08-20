export const REQUIRED_DSH_PEER_PACKAGES: readonly string[]

export function runtimePeerDependencies(dshVersion: string): Record<string, string>

export interface MissingRuntimePeer {
  name: string
  requiredBy: string[]
}

export function findMissingRuntimePeers(appDir: string): MissingRuntimePeer[]

export function assertRuntimePeerDependencies(appDir: string): void
