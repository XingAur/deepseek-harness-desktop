export type RuntimePluginCurrencyVerdict =
  | { ok: true }
  | { ok: false; reason: 'stale-manifest' }
  | { ok: false; reason: 'plugin-drift'; expected: string; actual: string }

export function compareManifest(expectedSha: string, manifest: unknown): RuntimePluginCurrencyVerdict
export function sha256File(path: string): string
export function resolveExpectedSha(repositoryRoot: string, runNpmCommand?: (cwd: string, args: string[]) => void): string
export function loadPublishedManifest(
  options: { repository: string; runtimeVersion: string; target: 'windows-x86_64' | 'darwin-aarch64' },
  ghApiFn?: (args: string[]) => { status: string; value?: string },
): Promise<{ status: 'found'; manifest: Record<string, unknown> } | { status: 'missing' }>
