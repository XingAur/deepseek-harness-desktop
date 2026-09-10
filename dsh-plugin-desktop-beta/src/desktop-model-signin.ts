/** Native sign-in relay between the authorization seam and the desktop shell. */

import { type Context, Service } from '@deepseek-ai/cordis'
import { AuthorizationDeclinedError, AuthorizationError } from '@deepseek-ai/dsh-authorization'
import type {
  AuthorizationEntry,
  AuthorizationInteraction,
  AuthorizationMethod,
  AuthorizationNotice,
} from '@deepseek-ai/dsh-authorization'
import { credentialKeyScope } from '@deepseek-ai/dsh-credentials'
import type { CredentialKey } from '@deepseek-ai/dsh-credentials'
import { desktopModelSigninCopy } from './model-signin-locale.ts'
import type { DesktopLocale } from './runtime.ts'

/** The credential scope the shipped LLM adapter family owns. */
const LLM_SIGNIN_SCOPE = 'llm-pi-ai'

/** The method this relay can run: device-code flows are notice-only, which native dialogs render. */
const NATIVE_METHOD = 'oauth'

/** One signable provider as the tray submenu renders it. */
export interface DesktopModelSigninFlow {
  readonly key: CredentialKey
  readonly label: string
  readonly methods: readonly AuthorizationMethod[]
  readonly inFlight: boolean
}

/** One native dialog the relay raises. */
export interface DesktopModelSigninDialog {
  readonly title: string
  readonly message: string
  readonly detail?: string
  readonly advisory?: string
  readonly buttons: readonly string[]
  readonly defaultId?: number
  readonly cancelId?: number
}

/** Native capabilities injected by the Electron launcher. */
export interface DesktopModelSigninBootstrap {
  /** Open a sign-in page in the user's browser. */
  openExternal(url: string): void
  /** Show one native dialog; resolves with the chosen button index. */
  showDialog(spec: DesktopModelSigninDialog): Promise<number>
  /** The locale the desktop shell currently presents. */
  locale(): DesktopLocale
}

/**
 * Relay the flows registered under the LLM adapter's credential scope to the
 * desktop tray: each flow starts one authorization attempt whose notices open
 * the browser and surface the device code through native dialogs. The seam is
 * read lazily because the LLM plugins compose after the launcher's own
 * services; a Profile without the capability simply offers nothing.
 */
export class DesktopModelSigninService extends Service {
  constructor(ctx: Context, private readonly bootstrap: DesktopModelSigninBootstrap) {
    super(ctx, 'desktopModelSignin')
  }

  /**
   * List the signable providers the mounted LLM adapter offers.
   * @returns one entry per registered flow under the LLM credential scope.
   */
  flows(): readonly DesktopModelSigninFlow[] {
    const authorization = this.ctx.get('authorization')
    if (!authorization) return []
    return authorization.list()
      .filter(entry => credentialKeyScope(entry.key) === LLM_SIGNIN_SCOPE)
      .map(entry => this.flowOf(entry))
  }

  /**
   * Run one sign-in attempt for a listed provider and report the outcome
   * through native dialogs.
   * @param entry - the flow the tray submenu selected.
   * @returns once the attempt settled and its outcome dialog was dismissed.
   */
  async beginSignIn(entry: DesktopModelSigninFlow): Promise<void> {
    const authorization = this.ctx.get('authorization')
    const copy = desktopModelSigninCopy(this.bootstrap.locale())
    if (!authorization) {
      await this.bootstrap.showDialog({
        title: copy.unavailableTitle,
        message: copy.unavailableMessage,
        buttons: [copy.dismiss],
        cancelId: 0,
      })
      return
    }
    if (!entry.methods.some(method => method.id === NATIVE_METHOD)) {
      await this.bootstrap.showDialog({
        title: entry.label,
        message: copy.noMethodMessage,
        buttons: [copy.dismiss],
        cancelId: 0,
      })
      return
    }
    try {
      const outcome = await authorization.begin({
        key: entry.key,
        method: NATIVE_METHOD,
        interaction: this.interaction(entry),
      })
      if (outcome.status === 'authorized') {
        await this.bootstrap.showDialog({
          title: entry.label,
          message: copy.successMessage,
          buttons: [copy.dismiss],
          cancelId: 0,
        })
      }
    } catch (cause) {
      await this.reportFailure(entry, cause, copy)
    }
  }

  /** @returns the tray-facing view of one registered flow. */
  private flowOf(entry: AuthorizationEntry): DesktopModelSigninFlow {
    return { key: entry.key, label: entry.label, methods: entry.methods, inFlight: entry.inFlight }
  }

  /**
   * Forward one running attempt's notices to the browser and native dialogs.
   * The seam never carries a secret through a notice, so the code is rendered
   * as plain dialog text. Progress notices without a page or code are dropped:
   * the device dialog already tells the human what to do.
   */
  private interaction(entry: DesktopModelSigninFlow): AuthorizationInteraction {
    const copy = desktopModelSigninCopy(this.bootstrap.locale())
    return {
      notify: (notice: AuthorizationNotice): void => {
        if (notice.url !== undefined) this.bootstrap.openExternal(notice.url)
        if (notice.code === undefined) return
        void this.bootstrap.showDialog({
          title: entry.label,
          message: notice.message.length > 0 ? notice.message : copy.deviceMessage,
          detail: copy.deviceCodeDetail(notice.code),
          advisory: copy.deviceAdvisory,
          buttons: [copy.dismiss, copy.cancelLogin],
          defaultId: 0,
          cancelId: 1,
        }).then(response => {
          if (response === 1) this.ctx.get('authorization')?.cancel(entry.key)
        }).catch(() => { /* a dismissed dialog must not fail the attempt it describes */ })
      },
      prompt: async prompt => {
        await this.bootstrap.showDialog({
          title: entry.label,
          message: copy.promptUnsupportedMessage,
          detail: prompt.message,
          buttons: [copy.dismiss],
          cancelId: 0,
        })
        throw new AuthorizationDeclinedError()
      },
    }
  }

  /** Map one failed attempt onto the copy its failure code names. */
  private async reportFailure(
    entry: DesktopModelSigninFlow,
    cause: unknown,
    copy: ReturnType<typeof desktopModelSigninCopy>,
  ): Promise<void> {
    if (cause instanceof AuthorizationError && cause.code === 'ALREADY_IN_FLIGHT') {
      await this.bootstrap.showDialog({
        title: entry.label,
        message: copy.inFlightMessage,
        buttons: [copy.dismiss],
        cancelId: 0,
      })
      return
    }
    const detail = cause instanceof Error ? cause.message : String(cause)
    await this.bootstrap.showDialog({
      title: entry.label,
      message: copy.failedMessage(detail),
      buttons: [copy.dismiss],
      cancelId: 0,
    })
  }
}

declare module '@deepseek-ai/cordis' {
  interface Context {
    /** Tray-facing sign-in relay for mounted LLM authorization flows. */
    desktopModelSignin: DesktopModelSigninService
  }
}

export default DesktopModelSigninService
