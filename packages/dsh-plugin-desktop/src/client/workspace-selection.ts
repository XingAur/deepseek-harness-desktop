import type { WorkspaceView } from './contracts'

/** Derive the current workspace id from the workspaces snapshot. */
export function currentWorkspaceIdOf(
  items: readonly WorkspaceView[],
  recentWorkspaceId: string | undefined,
): string | undefined {
  return recentWorkspaceId ?? items[0]?.workspaceId
}
