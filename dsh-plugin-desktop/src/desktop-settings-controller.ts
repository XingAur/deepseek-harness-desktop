/** Launcher-backed controller for the private Desktop settings API. */

import type {
  DesktopMarketProvider,
  DesktopMarketSnapshot,
} from './desktop-market.ts'
import type { DesktopProfileSummary } from './profile-manager.ts'
import type { DesktopProfiles } from './profile-service.ts'
import type {
  DesktopMarketSelectResponse,
  DesktopDeveloperToolsToggleResponse,
  DesktopDiagnosticsExportResponse,
  DesktopProfileCreateResponse,
  DesktopProfileDeleteResponse,
  DesktopProfileSelectResponse,
  DesktopRestartResponse,
  DesktopRecoveryRestartResponse,
  DesktopRendererReloadResponse,
  DesktopMcpStateRequest,
  DesktopMcpStateResponse,
  DesktopMcpTestRequest,
  DesktopMcpTestResponse,
  DesktopMcpWriteResponse,
  DesktopSettingsMarketView,
  DesktopSettingsProfileView,
  DesktopSkillsListResponse,
  DesktopSettingsResponse,
  DesktopSettingsWebView,
  DesktopTerminalOpenResponse,
} from './desktop-settings-contract.ts'
import {
  mergeDesktopMcpServer,
  parseDesktopMcpState,
  projectDesktopMcpServer,
  type DesktopMcpServerState,
} from './desktop-mcp.ts'

/** Launcher capabilities used without exposing their filesystem roots. */
export interface DesktopSettingsControllerBootstrap {
  /** Generation-scoped profile service. */
  readonly profiles: Pick<DesktopProfiles, 'current' | 'list' | 'create' | 'prepareSelection'>
    & Partial<Pick<DesktopProfiles, 'canDelete' | 'delete'>>
  /** Read the latest persisted request and the startup-effective provider. */
  readAa?(): { readonly requested: boolean; readonly effective: boolean }
  selectAa?(enabled: boolean): Promise<void>
  readMarket(): DesktopMarketSnapshot
  /** Persist an explicit provider request. */
  selectMarket(provider: DesktopMarketProvider): Promise<DesktopMarketSnapshot>
  /** Read marker-free URLs from the generation's actual WebServer and LAN snapshot. */
  readWeb(): DesktopSettingsWebView
  /** Queue an orderly restart after a response confirms persisted selection. */
  scheduleRestart(): void
  /** Queue an orderly restart into the pre-Host recovery assistant. */
  scheduleRecoveryRestart(): void
  /** Open the launcher-owned DSH terminal. */
  openTerminal(): void
  /** Reload the mounted renderer after its HTTP acknowledgement is delivered. */
  reloadRenderer(): void
  /** Toggle Developer Tools for the mounted renderer. */
  toggleDeveloperTools(): void
  /** Export diagnostics through the launcher-owned privacy flow. */
  exportDiagnostics(): void | Promise<void>
  /** Read the Host composition's skill catalog; absent when no registry is mounted. */
  readSkills?(): Promise<DesktopSkillsListResponse>
  /** Read the desktop-private MCP server state; absent when the launcher mounts no state path. */
  readMcp?(): readonly DesktopMcpServerState[]
  /** Persist the desktop-private MCP server state after validation. */
  writeMcp?(servers: readonly DesktopMcpServerState[]): Promise<void>
  /** Probe one MCP row with a real handshake; absent when MCP is not mounted. */
  probeMcp?(input: DesktopMcpTestRequest): Promise<DesktopMcpTestResponse>
}

/** A persisted response plus work that must run only after `res.end()`. */
export interface DesktopSettingsPostResponse<T extends object> {
  readonly response: T
  readonly afterResponse?: () => void | Promise<void>
}

/** Remove paths, bundle identities, and parser diagnostics from a profile. */
export function projectDesktopSettingsProfile(
  profile: DesktopProfileSummary,
  deletable = false,
): DesktopSettingsProfileView {
  return Object.freeze({
    name: profile.name,
    exists: profile.exists,
    webCapable: profile.webCapable,
    selectable: profile.exists && profile.webCapable && profile.problem === undefined,
    deletable,
  })
}

function projectMarket(
  value: DesktopMarketSnapshot,
  effective: DesktopMarketProvider,
): DesktopSettingsMarketView {
  return Object.freeze({
    requested: value.requested,
    effective,
    legacyDefaulted: value.legacyDefaulted,
  })
}

/**
 * Generation-scoped controller for Profile and Market preferences.
 *
 * The provider composed at startup remains `effective` for this controller's
 * lifetime. Persisting another provider changes only `requested` until the
 * queued restart creates a new Host generation.
 */
export class DesktopSettingsController {
  private readonly effectiveMarket: DesktopMarketProvider

  constructor(private readonly bootstrap: DesktopSettingsControllerBootstrap) {
    this.effectiveMarket = bootstrap.readMarket().effective
  }

  /** Read a fresh, renderer-safe settings projection. */
  read(): DesktopSettingsResponse {
    const web = this.bootstrap.readWeb()
    return Object.freeze({
      current: this.bootstrap.profiles.current.name,
      profiles: Object.freeze(
        this.bootstrap.profiles.list().map(profile => projectDesktopSettingsProfile(
          profile,
          this.bootstrap.profiles.canDelete?.(profile.name) ?? false,
        )),
      ),
      aa: Object.freeze(this.bootstrap.readAa?.() ?? { requested: false, effective: false }),
      market: projectMarket(this.bootstrap.readMarket(), this.effectiveMarket),
      web: Object.freeze({
        localUrl: web.localUrl,
        lanUrls: Object.freeze([...web.lanUrls]),
        lanState: web.lanState,
        lanError: web.lanError,
        lanCaFingerprint: web.lanCaFingerprint,
        lanCaUrls: Object.freeze([...web.lanCaUrls]),
      }),
    })
  }

  /** Create one safe profile without selecting it or requesting restart. */
  createProfile(name: string): DesktopProfileCreateResponse {
    this.bootstrap.profiles.create(name)
    return this.read()
  }

  /** Delete one inactive user profile and return the fresh settings state. */
  async deleteProfile(name: string): Promise<DesktopProfileDeleteResponse> {
    if (this.bootstrap.profiles.delete === undefined) {
      throw new Error('dsh-plugin-desktop: profile deletion is unavailable')
    }
    await this.bootstrap.profiles.delete(name)
    return this.read()
  }

  /** Persist a fresh compatible profile, deferring restart until after response. */
  async selectProfile(
    name: string,
  ): Promise<DesktopSettingsPostResponse<DesktopProfileSelectResponse>> {
    const selection = await this.bootstrap.profiles.prepareSelection(name)
    return Object.freeze({
      response: Object.freeze({ accepted: true, restartRequired: selection.restartRequired }),
      ...(selection.restartRequired ? { afterResponse: () => selection.restart() } : {}),
    })
  }

  /** Persist a provider and defer restart until after the response is ended. */
  async selectMarket(
    provider: DesktopMarketProvider,
  ): Promise<DesktopSettingsPostResponse<DesktopMarketSelectResponse>> {
    await this.bootstrap.selectMarket(provider)
    const restartRequired = provider !== this.effectiveMarket
    return Object.freeze({
      response: Object.freeze({ accepted: true, restartRequired }),
      ...(restartRequired ? { afterResponse: () => { this.bootstrap.scheduleRestart() } } : {}),
    })
  }

  async selectAa(enabled: boolean): Promise<DesktopSettingsPostResponse<DesktopMarketSelectResponse>> {
    if (!this.bootstrap.selectAa || !this.bootstrap.readAa) throw new Error('AA selection is unavailable')
    await this.bootstrap.selectAa(enabled)
    const restartRequired = enabled !== this.bootstrap.readAa().effective
    return Object.freeze({
      response: Object.freeze({ accepted: true, restartRequired }),
      ...(restartRequired ? { afterResponse: () => { this.bootstrap.scheduleRestart() } } : {}),
    })
  }

  /** Open the native terminal through the launcher-owned action. */
  openTerminal(): DesktopTerminalOpenResponse {
    this.bootstrap.openTerminal()
    return Object.freeze({ accepted: true })
  }

  /** Read the Host skill catalog, or an unavailable projection when no registry is mounted. */
  async listSkills(): Promise<DesktopSkillsListResponse> {
    if (this.bootstrap.readSkills === undefined) {
      return Object.freeze({ available: false, skills: Object.freeze([]) })
    }
    const value = await this.bootstrap.readSkills()
    return Object.freeze({
      available: value.available,
      skills: Object.freeze([...value.skills].map(skill => Object.freeze({ ...skill }))),
    })
  }

  /** Project the desktop-managed MCP server rows without secret values. */
  listMcp(): DesktopMcpStateResponse {
    if (this.bootstrap.readMcp === undefined) {
      return Object.freeze({ servers: Object.freeze([]), restartRequired: true })
    }
    const servers = this.bootstrap.readMcp()
    return Object.freeze({
      servers: Object.freeze(servers.map(projectDesktopMcpServer)),
      restartRequired: true,
    })
  }

  /**
   * Validate, merge, and persist MCP rows; the launcher restart follows the
   * HTTP acknowledgement because rows only load at composition time.
   */
  async writeMcp(request: DesktopMcpStateRequest): Promise<DesktopSettingsPostResponse<DesktopMcpWriteResponse>> {
    if (this.bootstrap.writeMcp === undefined || this.bootstrap.readMcp === undefined) {
      throw new Error('desktop MCP state is not mounted')
    }
    const seen = new Set<string>()
    for (const row of request.servers) {
      if (seen.has(row.id)) throw new Error('duplicate MCP server id')
      seen.add(row.id)
    }
    const existing = this.bootstrap.readMcp()
    const merged = request.servers.map(row => mergeDesktopMcpServer(
      existing.find(candidate => candidate.id === row.id),
      row,
    ))
    parseDesktopMcpState({ version: 1, servers: merged })
    await this.bootstrap.writeMcp(merged)
    return Object.freeze({
      response: Object.freeze({
        accepted: true,
        servers: Object.freeze(merged.map(projectDesktopMcpServer)),
        restartScheduled: true,
      }),
      afterResponse: () => { this.bootstrap.scheduleRestart() },
    })
  }

  /**
   * Probe one MCP row without persisting anything. When the request carries
   * the id of a stored row, its stored secret values join the probe.
   */
  async testMcp(request: DesktopMcpTestRequest): Promise<DesktopMcpTestResponse> {
    if (this.bootstrap.probeMcp === undefined) {
      throw new Error('desktop MCP state is not mounted')
    }
    return this.bootstrap.probeMcp(request)
  }

  /** Acknowledge the renderer before queueing an orderly Desktop relaunch. */
  restart(): DesktopSettingsPostResponse<DesktopRestartResponse> {
    return Object.freeze({
      response: Object.freeze({ accepted: true }),
      afterResponse: () => { this.bootstrap.scheduleRestart() },
    })
  }

  /** Acknowledge the renderer before queueing a recovery-mode relaunch. */
  restartToRecovery(): DesktopSettingsPostResponse<DesktopRecoveryRestartResponse> {
    return Object.freeze({
      response: Object.freeze({ accepted: true }),
      afterResponse: () => { this.bootstrap.scheduleRecoveryRestart() },
    })
  }

  /** Acknowledge the renderer before replacing its current document. */
  reloadRenderer(): DesktopSettingsPostResponse<DesktopRendererReloadResponse> {
    return Object.freeze({
      response: Object.freeze({ accepted: true }),
      afterResponse: () => { this.bootstrap.reloadRenderer() },
    })
  }

  /** Toggle Developer Tools without exposing an Electron bridge to the page. */
  toggleDeveloperTools(): DesktopDeveloperToolsToggleResponse {
    this.bootstrap.toggleDeveloperTools()
    return Object.freeze({ accepted: true })
  }

  /** Export diagnostics through the native confirmation and reveal flow. */
  async exportDiagnostics(): Promise<DesktopDiagnosticsExportResponse> {
    await this.bootstrap.exportDiagnostics()
    return Object.freeze({ accepted: true })
  }
}

declare module '@deepseek-ai/cordis' {
  interface Context {
    /** Launcher-owned controller behind the private loopback settings API. */
    desktopSettingsController: DesktopSettingsController
  }
}

export default DesktopSettingsController
