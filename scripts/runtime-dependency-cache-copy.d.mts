export interface RuntimeDependencyCacheVersions {
  dshVersion: string
  pnpmVersion: string
}

export interface RuntimeDependencyCacheCopyAdapters {
  existsSync?: (path: string) => boolean
  readFileSync?: (path: string, encoding: 'utf8') => string
  cpSync?: (
    source: string,
    destination: string,
    options: { recursive: true; verbatimSymlinks: true },
  ) => void
}

export function restoreRuntimeDependencyCache(
  appDirectory: string,
  cacheValue: string | undefined,
  versions: RuntimeDependencyCacheVersions,
  adapters?: RuntimeDependencyCacheCopyAdapters,
): boolean
