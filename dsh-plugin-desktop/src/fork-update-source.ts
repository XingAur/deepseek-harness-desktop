/**
 * Fork-owned update source selection overriding upstream DSH release services.
 *
 * Upstream DSH Desktop checks versions and downloads installers from the
 * official release service (`dshdesktop.cn`). A fork must not direct its users
 * to upstream installers: installing one replaces the fork and discards every
 * fork-owned change. This module is the single place that decides where
 * update checks and installer downloads go.
 *
 * - `inherit`: keep the upstream release service (stock behavior).
 * - `disabled`: no update checks, no tray command, no download offers.
 * - `custom`: point checks and downloads at a fork-owned service. The version
 *   endpoint must answer the upstream response contract: HTTP 200 JSON with a
 *   canonical SemVer `version`, optional `channel` (`'stable' | 'beta'`).
 */
export type ForkUpdateSource =
  | { readonly kind: 'inherit' }
  | { readonly kind: 'disabled' }
  | {
      readonly kind: 'custom'
      /** Version-check endpoint replacing the upstream fixed endpoint. */
      readonly versionEndpoint: string
      /** Installer download endpoints replacing the upstream fixed endpoints. */
      readonly downloadEndpoints: Readonly<Record<'darwin' | 'win32', string>>
    }

/**
 * The fork's update source: the self-hosted relay/update server. China-local
 * delivery avoids GitHub connectivity problems; the endpoints answer the
 * upstream contracts verbatim (version JSON + raw installer bytes). Flip to
 * `disabled` to turn every update surface off again.
 */
export const FORK_UPDATE_SOURCE: ForkUpdateSource = {
  kind: 'custom',
  versionEndpoint: 'https://8.147.62.187/updates/latest.json',
  downloadEndpoints: {
    win32: 'https://8.147.62.187/updates/DSH-Desktop-windows-latest.exe',
    darwin: 'https://8.147.62.187/updates/DSH-Desktop-mac-latest.dmg',
  },
}

/** Concrete endpoints handed to the update seams, or undefined when disabled. */
export interface ResolvedForkUpdateEndpoints {
  readonly versionEndpoint: string
  readonly downloadEndpoints: Readonly<Record<'darwin' | 'win32', string>>
}

/**
 * Resolve the endpoint pair the update seams must use.
 * @param upstreamVersionEndpoint - upstream version-check endpoint.
 * @param upstreamDownloadEndpoints - upstream installer download endpoints.
 * @returns resolved endpoints, or undefined when updates are disabled.
 */
export function resolveForkUpdateEndpoints(
  upstreamVersionEndpoint: string,
  upstreamDownloadEndpoints: Readonly<Record<'darwin' | 'win32', string>>,
): ResolvedForkUpdateEndpoints | undefined {
  if (FORK_UPDATE_SOURCE.kind === 'disabled') return undefined
  if (FORK_UPDATE_SOURCE.kind === 'inherit') {
    return {
      versionEndpoint: upstreamVersionEndpoint,
      downloadEndpoints: upstreamDownloadEndpoints,
    }
  }
  return {
    versionEndpoint: FORK_UPDATE_SOURCE.versionEndpoint,
    downloadEndpoints: FORK_UPDATE_SOURCE.downloadEndpoints,
  }
}
