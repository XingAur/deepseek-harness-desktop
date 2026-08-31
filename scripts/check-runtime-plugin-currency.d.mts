export type RuntimePluginCurrencyVerdict =
  | { ok: true }
  | { ok: false; reason: 'stale-manifest' }
  | { ok: false; reason: 'plugin-drift'; expected: string; actual: string }

export function compareManifest(expectedSha: string, manifest: unknown, runtimeTag?: string): RuntimePluginCurrencyVerdict
export function sha256File(path: string): string
export function currentPluginSha256(repositoryRoot: string, runNpmCommand?: (cwd: string, args: string[]) => void): string
export function fetchRuntimeManifest(
  target: 'windows-x86_64' | 'darwin-aarch64',
  runtimeTag: string,
  token?: string,
  options?: Record<string, unknown>,
): Promise<Record<string, unknown> | null>
