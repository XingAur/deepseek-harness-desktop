/** Cordis Host plugin for scheduled and interactive DSH Desktop updates. */

import type { Context } from '@deepseek-ai/cordis'
import z from '@deepseek-ai/schemastery'
import type {} from '@deepseek-ai/dsh-client-connection'
import type {} from '@deepseek-ai/dsh-host-webserver'
import {
  DESKTOP_UPDATE_CHECK_PATH,
  DESKTOP_UPDATE_DOWNLOAD_PATH,
  DESKTOP_UPDATE_STATE_PATH,
  type DesktopUpdateStateResponse,
} from './desktop-settings-contract.ts'
import {
  handleDesktopUpdateCheckRequest,
  handleDesktopUpdateDownloadRequest,
  handleDesktopUpdateStateRequest,
} from './desktop-settings-route.ts'
import { resolveForkUpdateEndpoints } from './fork-update-source.ts'
import type {} from './runtime.ts'
import { DESKTOP_DOWNLOAD_URLS } from './update-download.ts'
import { startDesktopUpdateLifecycle } from './update-lifecycle.ts'
import { DESKTOP_VERSION_ENDPOINT } from './update-checker.ts'

/** Stable Cordis plugin name. */
export const name = 'desktop-updates'

/** Context key carrying the renderer-facing update facade. */
export const DESKTOP_UPDATE_FACADE = 'desktopUpdateFacade'

/** Renderer-safe update status and confirmed-download entry point. */
export interface DesktopUpdateFacade {
  /** Point-in-time status for the running executable. */
  status(): DesktopUpdateStateResponse
  /** Begin the confirmed download flow; false when downloads cannot start. */
  requestDownload(version: string): boolean
}

declare module '@deepseek-ai/cordis' {
  interface Context {
    /** Present only when the fork update source is enabled. */
    [DESKTOP_UPDATE_FACADE]?: DesktopUpdateFacade
  }
}

/** Native adapter required for network, tray, confirmation, and installer access. */
export const inject = ['desktopRuntime', 'webServer', 'connection', 'settings']

const MAX_TIMER_DELAY_MS = 2_147_483_647

/** Scheduled update policy. */
export interface Config {
  /** Enable background checks in packaged applications. */
  enabled: boolean
  /** Delay before the first background check after plugin activation. */
  initialDelayMs: number
  /** Delay between completion of one background check and the next attempt. */
  intervalMs: number
  /** Maximum duration of one version request before caller-owned cancellation. */
  requestTimeoutMs: number
}

/** Validated scheduled update policy. */
export const Config: z<Config> = z.object({
  enabled: z.boolean().default(true),
  initialDelayMs: z.number().step(1).min(0).max(MAX_TIMER_DELAY_MS).default(60_000),
  intervalMs: z.number().step(1).min(1).max(MAX_TIMER_DELAY_MS).default(6 * 60 * 60 * 1000),
  requestTimeoutMs: z.number().step(1).min(1).max(MAX_TIMER_DELAY_MS).default(15_000),
})

/**
 * Register effect-scoped update polling and its dynamic tray command.
 * @param ctx - Host context carrying the desktop native adapter.
 * @param config - validated polling and timeout values.
 */
export function apply(ctx: Context, config: Config): void {
  const endpoints = resolveForkUpdateEndpoints(DESKTOP_VERSION_ENDPOINT, DESKTOP_DOWNLOAD_URLS)
  if (endpoints === undefined) {
    // The fork disabled upstream update services: no polling, no tray command,
    // no manual-check route, and no installer download offers exist at all.
    ctx.logger.info('dsh-plugin-desktop: fork update source is disabled; update checks stay off')
    return
  }
  ctx.effect(() => {
    const adapter = ctx.desktopRuntime.updates
    // The scheduled-polling half follows the user's desktop setting live; the
    // manual check, tray command, and renderer routes stay mounted either way.
    const scheduledEnabled = (): boolean => {
      const settings = ctx.settings?.get?.('dsh-desktop') as { autoUpdateCheck?: unknown } | undefined
      return settings?.autoUpdateCheck !== false
    }
    const lifecycle = startDesktopUpdateLifecycle({
      adapter,
      policy: { ...config, enabled: config.enabled && scheduledEnabled() },
      locale: () => ctx.desktopRuntime.locale,
      registerTrayItem: item => ctx.desktopRuntime.registerTrayItem(item),
      endpoints,
    })
    const offSettings = ctx.on?.('settings/updated', (namespace: string, next: unknown) => {
      if (namespace !== 'dsh-desktop') return
      lifecycle.setScheduledEnabled(config.enabled && (next as { autoUpdateCheck?: unknown } | undefined)?.autoUpdateCheck !== false)
    })
    const rendererOrigin = `http://127.0.0.1:${String(ctx.webServer.port)}`
    const unregister = ctx.webServer.register({
      kind: 'exact',
      path: DESKTOP_UPDATE_CHECK_PATH,
      handler: (req, res) => {
        const rejection = ctx.connection?.requestRejection?.(req)
        if (rejection !== undefined) {
          res.writeHead(rejection)
          res.end(rejection === 401 ? 'unauthorized' : 'forbidden')
          return
        }
        return handleDesktopUpdateCheckRequest(
          req,
          res,
          rendererOrigin,
          () => lifecycle.checkNow(),
          (operation, cause) => {
            ctx.logger.error(
              `dsh-plugin-desktop: failed to ${operation}: ${cause instanceof Error ? cause.message : String(cause)}`,
            )
          },
        )
      },
    })
    const facade: DesktopUpdateFacade = {
      status: () => {
        const snapshot = lifecycle.snapshot()
        return {
          currentVersion: adapter.currentVersion,
          channel: adapter.releaseChannel ?? null,
          canDownload: adapter.canDownload,
          availableVersion: snapshot.availableVersion ?? null,
          checking: snapshot.checking,
          downloadingVersion: snapshot.downloadingVersion ?? null,
        }
      },
      requestDownload: version => lifecycle.requestDownload(version),
    }
    ctx.provide(DESKTOP_UPDATE_FACADE, facade)
    const unregisterState = ctx.webServer.register({
      kind: 'exact',
      path: DESKTOP_UPDATE_STATE_PATH,
      handler: (req, res) => {
        return handleDesktopUpdateStateRequest(
          req,
          res,
          rendererOrigin,
          () => facade.status(),
          (operation, cause) => {
            ctx.logger.error(
              `dsh-plugin-desktop: failed to ${operation}: ${cause instanceof Error ? cause.message : String(cause)}`,
            )
          },
        )
      },
    })
    const unregisterDownload = ctx.webServer.register({
      kind: 'exact',
      path: DESKTOP_UPDATE_DOWNLOAD_PATH,
      handler: (req, res) => {
        return handleDesktopUpdateDownloadRequest(
          req,
          res,
          rendererOrigin,
          facade.requestDownload,
          (operation, cause) => {
            ctx.logger.error(
              `dsh-plugin-desktop: failed to ${operation}: ${cause instanceof Error ? cause.message : String(cause)}`,
            )
          },
        )
      },
    })
    return async () => {
      offSettings?.()
      unregisterDownload()
      unregisterState()
      unregister()
      await lifecycle.dispose()
    }
  }, 'dsh-plugin-desktop: update polling, confirmation, and installer handoff')
}
