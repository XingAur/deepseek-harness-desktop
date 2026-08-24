export interface RuntimeDependencyCacheOptions {
  candidates: readonly string[]
  dshVersion: string
  pnpmVersion: string
}

export function findCompatibleRuntimeDependencyCache(
  options: RuntimeDependencyCacheOptions,
): string | undefined
