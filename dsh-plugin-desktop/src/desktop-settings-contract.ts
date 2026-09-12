/** Private same-origin Desktop settings API shared with the bundled renderer. */

import type { DesktopMarketProvider } from './desktop-market.ts'

/** Read the current Desktop-owned settings state. */
export const DESKTOP_SETTINGS_PATH = '/api/desktop/settings'

/** Create one safe Web profile without selecting it. */
export const DESKTOP_PROFILE_CREATE_PATH = '/api/desktop/profiles/create'

/** Select one compatible profile for the next Desktop generation. */
export const DESKTOP_PROFILE_SELECT_PATH = '/api/desktop/profiles/select'

/** Delete one inactive, user-created Web Profile. */
export const DESKTOP_PROFILE_DELETE_PATH = '/api/desktop/profiles/delete'

/** Persist the Market provider selected for the next Desktop generation. */
export const DESKTOP_AA_SELECT_PATH = '/api/desktop/aa/select'

export const DESKTOP_MARKET_SELECT_PATH = '/api/desktop/market/select'

/** Open the launcher-owned DSH terminal without accepting command text. */
export const DESKTOP_TERMINAL_OPEN_PATH = '/api/desktop/terminal/open'

/** Queue an orderly Desktop relaunch after acknowledging the renderer. */
export const DESKTOP_RESTART_PATH = '/api/desktop/restart'

/** Queue an orderly relaunch that opens the recovery assistant before Host boot. */
export const DESKTOP_RECOVERY_RESTART_PATH = '/api/desktop/restart/recovery'

/** Reload the renderer through the launcher without exposing Electron APIs. */
export const DESKTOP_RENDERER_RELOAD_PATH = '/api/desktop/developer/reload'

/** Toggle the mounted window's Developer Tools through the launcher. */
export const DESKTOP_DEVELOPER_TOOLS_TOGGLE_PATH = '/api/desktop/developer/devtools'

/** Run the generation-owned manual update check. */
export const DESKTOP_UPDATE_CHECK_PATH = '/api/desktop/updates/check'
export const DESKTOP_UPDATE_STATE_PATH = '/api/desktop/updates/state'
export const DESKTOP_UPDATE_DOWNLOAD_PATH = '/api/desktop/updates/download'

/** Read or write this application's OS login-item state. */

/** Export one local diagnostic archive through the launcher-owned flow. */
export const DESKTOP_DIAGNOSTICS_EXPORT_PATH = '/api/desktop/diagnostics/export'

/** Read the skill catalog visible to the running Host composition. */
export const DESKTOP_SKILLS_LIST_PATH = '/api/desktop/skills'

/** Read and replace the desktop-managed MCP server rows. */
export const DESKTOP_MCP_STATE_PATH = '/api/desktop/mcp'

/** Renderer-safe projection of one discovered profile. */
export interface DesktopSettingsProfileView {
  /** Profile name accepted by the launcher. */
  readonly name: string
  /** Whether its manifest already exists on disk. */
  readonly exists: boolean
  /** Whether it contains the Web application required by Desktop. */
  readonly webCapable: boolean
  /** Whether the launcher can select it. */
  readonly selectable: boolean
  /** Whether the profile can be removed without affecting recovery state. */
  readonly deletable: boolean
}

/** Requested and generation-effective Market provider state. */
export interface DesktopSettingsMarketView {
  /** Explicit or fail-safe provider requested on disk. */
  readonly requested: DesktopMarketProvider
  /** Provider composed into the currently running generation. */
  readonly effective: DesktopMarketProvider
  /** Whether an absent or invalid legacy state produced the fail-safe default. */
  readonly legacyDefaulted: boolean
}

/** Marker-free ordinary-browser URLs for the running Web generation. */
export interface DesktopSettingsWebView {
  /** Always-available loopback URL using the actual listening port. */
  readonly localUrl: string
  /** Authenticated HTTPS URLs; non-empty only while the LAN edge is ready. */
  readonly lanUrls: readonly string[]
  /** Actual hot edge state, distinct from the persisted LAN preference. */
  readonly lanState: 'inactive' | 'starting' | 'ready' | 'failed'
  /** Stable certificate/bind failure category, present only for a failed edge. */
  readonly lanError: string | null
  /** SHA-256 identity for the installation-local CA, when available. */
  readonly lanCaFingerprint: string | null
  /** Public CA downloads on each ready HTTPS authority, without auth tokens. */
  readonly lanCaUrls: readonly string[]
}

/** Complete renderer-safe Desktop settings state. */
export interface DesktopSettingsResponse {
  /** Profile backing the currently running generation. */
  readonly current: string
  /** Fresh profile discovery without filesystem paths or manifest details. */
  readonly profiles: readonly DesktopSettingsProfileView[]
  /** Market choice for the current and next generation. */
  readonly aa: { readonly requested: boolean; readonly effective: boolean }
  readonly market: DesktopSettingsMarketView
  /** Actual browser URLs for the current WebServer generation. */
  readonly web: DesktopSettingsWebView
}

/** Exact body accepted by the profile-creation endpoint. */
export interface DesktopProfileCreateRequest {
  readonly name: string
}

/** Successful creation returns a fresh state that includes the new profile. */
export type DesktopProfileCreateResponse = DesktopSettingsResponse

/** Exact body accepted by the profile-selection endpoint. */
export interface DesktopProfileSelectRequest {
  readonly name: string
}

/** Successful persisted selection returned before the Host restarts. */
export interface DesktopRestartAcceptance {
  readonly accepted: true
  readonly restartRequired: boolean
}

/** Successful profile selection handoff. */
export type DesktopProfileSelectResponse = DesktopRestartAcceptance

/** Exact body accepted by the profile-deletion endpoint. */
export interface DesktopProfileDeleteRequest {
  readonly name: string
}

/** Successful deletion returns a fresh state without the removed profile. */
export type DesktopProfileDeleteResponse = DesktopSettingsResponse

/** Exact body accepted by the Market-provider endpoint. */
export interface DesktopMarketSelectRequest {
  readonly provider: DesktopMarketProvider
}

/** Successful Market selection handoff. */
export type DesktopMarketSelectResponse = DesktopRestartAcceptance

/** Exact empty body accepted by the terminal endpoint. */
export type DesktopTerminalOpenRequest = Readonly<Record<string, never>>

/** Successful handoff to the launcher-owned terminal action. */
export interface DesktopTerminalOpenResponse {
  readonly accepted: true
}

/** Exact empty body accepted by the explicit restart endpoint. */
export type DesktopRestartRequest = Readonly<Record<string, never>>

/** Successful handoff to the launcher's orderly relaunch flow. */
export interface DesktopRestartResponse {
  readonly accepted: true
}

/** Exact empty body accepted by the recovery-restart endpoint. */
export type DesktopRecoveryRestartRequest = Readonly<Record<string, never>>

/** Successful handoff to the launcher's recovery relaunch flow. */
export type DesktopRecoveryRestartResponse = DesktopRestartResponse

/** Exact empty body accepted by the renderer-reload endpoint. */
export type DesktopRendererReloadRequest = Readonly<Record<string, never>>

/** Successful handoff to the launcher-owned renderer reload. */
export interface DesktopRendererReloadResponse {
  readonly accepted: true
}

/** Exact empty body accepted by the Developer Tools endpoint. */
export type DesktopDeveloperToolsToggleRequest = Readonly<Record<string, never>>

/** Successful handoff to the launcher-owned Developer Tools action. */
export interface DesktopDeveloperToolsToggleResponse {
  readonly accepted: true
}

/** Exact empty body accepted by the manual update-check endpoint. */
export type DesktopUpdateCheckRequest = Readonly<Record<string, never>>

/** Successful completion of the interactive update-check flow. */
export interface DesktopUpdateCheckResponse {
  readonly accepted: true
}

/** Exact empty body accepted by the diagnostic-export endpoint. */
export type DesktopDiagnosticsExportRequest = Readonly<Record<string, never>>

/** Successful handoff to the launcher-owned diagnostic export flow. */
export interface DesktopDiagnosticsExportResponse {
  readonly accepted: true
}

/** Exact empty body accepted by the skill-catalog endpoint. */
export type DesktopSkillsListRequest = Readonly<Record<string, never>>

/** Renderer-safe projection of one discovered skill. */
export interface DesktopSkillsListItem {
  /** Kebab-case skill name from the skill frontmatter. */
  readonly name: string
  /** Short routing description shown by discovery consumers. */
  readonly description: string
  /** Optional extra routing guidance from the skill frontmatter. */
  readonly whenToUse: string | null
  /** Whether model-facing catalogs include this skill. */
  readonly modelInvocable: boolean
  /** Whether human-facing catalogs include this skill. */
  readonly userInvocable: boolean
  /** Discovery root that produced this skill, e.g. `user-agents`. */
  readonly source: string
}

/** Successful skill-catalog read; `available` is false when the Host composition mounts no skill registry. */
export interface DesktopSkillsListResponse {
  readonly available: boolean
  readonly skills: readonly DesktopSkillsListItem[]
}

/** Transport accepted by the desktop MCP rows. */
export type DesktopMcpTransport = 'stdio' | 'streamable-http'

/** Exact body accepted by the MCP state endpoint. */
export interface DesktopMcpStateRequest {
  readonly servers: readonly {
    readonly id: string
    readonly serverName: string
    readonly transport: DesktopMcpTransport
    readonly command?: string
    readonly args?: readonly string[]
    /** `null` deletes a stored key; absent keys keep their stored value. */
    readonly env?: Readonly<Record<string, string | null>>
    readonly cwd?: string
    readonly url?: string
    /** `null` deletes a stored header; absent keys keep their stored value. */
    readonly headers?: Readonly<Record<string, string | null>>
    readonly disabled?: boolean
  }[]
}

/** Renderer-safe projection of one desktop-managed MCP server row. */
export interface DesktopMcpServerView {
  /** Stable row identity. */
  readonly id: string
  /** Tool namespace segment shown to the user. */
  readonly serverName: string
  /** Transport selecting which config fields are meaningful. */
  readonly transport: DesktopMcpTransport
  /** stdio: executable to spawn. */
  readonly command: string | null
  /** stdio: argument vector. */
  readonly args: readonly string[]
  /** stdio: extra environment keys (values are never echoed). */
  readonly envKeys: readonly string[]
  /** stdio: working directory. */
  readonly cwd: string | null
  /** streamable-http: endpoint URL. */
  readonly url: string | null
  /** streamable-http: header names (values are never echoed). */
  readonly headerKeys: readonly string[]
  /** Disabled rows stay configured but are not injected into the profile. */
  readonly disabled: boolean
}

/** Successful MCP state read; the write always requires a Host restart. */
export interface DesktopMcpStateResponse {
  readonly servers: readonly DesktopMcpServerView[]
  /** Row changes apply after the launcher restarts the Host composition. */
  readonly restartRequired: true
}

/** Successful MCP state write acceptance. */
export interface DesktopMcpWriteResponse {
  readonly accepted: true
  readonly servers: readonly DesktopMcpServerView[]
  /** The launcher schedules the profile restart after accepting the write. */
  readonly restartScheduled: true
}

/** Stable API path for probing one MCP server row without saving it. */
export const DESKTOP_MCP_TEST_PATH = '/api/desktop/mcp/test'

/** Probe well-known loopback HTTP proxy ports without saving a setting. */
export const DESKTOP_PROXY_DETECT_PATH = '/api/desktop/proxy/detect'

/** Probe whether a drafted proxy can reach api.x.ai without saving a setting. */
export const DESKTOP_PROXY_TEST_PATH = '/api/desktop/proxy/test'

/** Renderer-safe outcome of a local outbound-proxy probe. */
export interface DesktopProxyDetectResponse {
  readonly found: boolean
  readonly proxyUrl: string
}

/** Exact body accepted by the model-proxy test endpoint. */
export interface DesktopProxyTestRequest {
  readonly proxyUrl: string
}

/** Renderer-safe outcome of a model-proxy reachability probe. */
export interface DesktopProxyTestResponse {
  readonly ok: boolean
  readonly code: string
}

/** Exact body accepted by the MCP test endpoint. */
export interface DesktopMcpTestRequest {
  /**
   * When present and matching a stored row, its stored secret values join
   * the probe; the provided row fields override the stored ones.
   */
  readonly id?: string
  readonly row: DesktopMcpStateRequest['servers'][number]
}

/** Probe outcome for one MCP row; details never echo secrets or stderr. */
export interface DesktopMcpTestResponse {
  readonly ok: boolean
  /** Tools the server advertised, when it answered in time. */
  readonly toolCount?: number
  /** Server-reported name from the MCP initialize result. */
  readonly serverInfoName?: string
  /** Server-reported version from the MCP initialize result. */
  readonly serverInfoVersion?: string
  /** Stable failure category. */
  readonly error?: 'timeout' | 'spawn' | 'connect' | 'protocol' | 'http-status'
  /** Short human-readable hint safe to show in the settings page. */
  readonly detail?: string
}

/** Stable API failure shape that never contains native paths or raw causes. */
/** Renderer-safe projection of the update coordinator for the settings client. */
export interface DesktopUpdateStateResponse {
  readonly currentVersion: string
  readonly channel: 'stable' | 'beta' | null
  readonly canDownload: boolean
  readonly availableVersion: string | null
  readonly checking: boolean
  readonly downloadingVersion: string | null
}

/** Confirmed-download request for one advertised version. */
export interface DesktopUpdateDownloadRequest {
  readonly version: string
}

/** Accepted marker for a background installer download. */
export interface DesktopUpdateDownloadResponse {
  readonly accepted: boolean
}

export interface DesktopSettingsErrorResponse {
  readonly error: string
}
