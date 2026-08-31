export type RuntimeReleaseState =
  | { state: 'absent' | 'empty' }
  | { state: 'complete'; releaseId: number; archiveAssetId: number; manifestAssetId: number }

export function resolveRuntimeReleaseState(options: Record<string, unknown>): Promise<RuntimeReleaseState>
