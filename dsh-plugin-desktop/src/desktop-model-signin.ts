/** Native sign-in relay between the authorization seam and the desktop shell. */

import { type Context, Service } from '@deepseek-ai/cordis'
import { AuthorizationDeclinedError, AuthorizationError } from '@deepseek-ai/dsh-authorization'
import type {
  AuthorizationEntry,
  AuthorizationInteraction,
  AuthorizationMethod,
  AuthorizationNotice,
  AuthorizationPrompt,
} from '@deepseek-ai/dsh-authorization'
import { credentialKeyId, credentialKeyScope } from '@deepseek-ai/dsh-credentials'
import type { CredentialKey } from '@deepseek-ai/dsh-credentials'
import { desktopModelSigninCopy } from './model-signin-locale.ts'
import type { DesktopLocale } from './runtime.ts'

/** The credential scope the shipped LLM adapter family owns. */
const LLM_SIGNIN_SCOPE = 'llm-pi-ai'

/**
 * Whether the resolved llm-pi-ai section already names this catalog route.
 * A stored OAuth grant is not itself a Models-page row; the row appears only
 * once a profile exists under `providers.<id>`.
 * @param section - the resolved `llm-pi-ai` settings value, if any.
 * @param providerId - the catalog provider route, such as `xai`.
 * @returns true when that route already has a profile.
 */
function hasConfiguredProvider(section: unknown, providerId: string): boolean {
  if (section === null || typeof section !== 'object' || Array.isArray(section)) return false
  const providers = (section as { providers?: unknown }).providers
  if (providers === null || typeof providers !== 'object' || Array.isArray(providers)) return false
  return Object.hasOwn(providers, providerId)
}

/** The method this relay can run: OAuth notices open a browser; select prompts become dialog buttons. */
const NATIVE_METHOD = 'oauth'

/** Native dialogs allow 4 buttons; one is reserved for cancel. */
const MAX_SELECT_OPTIONS = 3

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
  /** Closes the dialog as cancel when a raced prompt is withdrawn. */
  readonly signal?: AbortSignal
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
        await this.ensureSignedInProviderRoute(entry.key)
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
   * Write an empty catalog profile for a provider that just signed in, so the
   * Models page and session picker can use the stored credential. Login itself
   * only writes `llm-pi-ai/<id>`; without a `providers.<id>` profile the route
   * never appears. An existing profile is left untouched. A settings write
   * failure is logged and does not retract the saved credential.
   * @param key - the credential key whose id is the catalog provider route.
   */
  private async ensureSignedInProviderRoute(key: CredentialKey): Promise<void> {
    const settings = this.ctx.get('settings')
    if (settings === undefined) return
    const providerId = credentialKeyId(key)
    if (hasConfiguredProvider(settings.get(LLM_SIGNIN_SCOPE), providerId)) return
    try {
      await settings.update(LLM_SIGNIN_SCOPE, { providers: { [providerId]: {} } })
    } catch (cause) {
      this.ctx.logger.warn(
        `dsh-plugin-desktop: signed-in provider "${providerId}" is stored, but adding its Models route failed`,
      )
      this.ctx.logger.warn(cause)
    }
  }

  /**
   * Forward one running attempt's notices to the browser and native dialogs.
   * The seam never carries a secret through a notice, so the code is rendered
   * as plain dialog text. Progress notices without a page or code are dropped:
   * the device dialog already tells the human what to do.
   *
   * Select prompts become option buttons. A text/secret prompt with a withdrawal
   * signal is the browser-callback race: native dialogs cannot take the paste,
   * so the dialog waits until the callback wins or the human cancels.
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
      prompt: prompt => this.answerPrompt(entry, prompt, copy),
    }
  }

  /**
   * Answer one authorization prompt through native dialogs, or decline it.
   * @param entry - the flow whose attempt asked the question.
   * @param prompt - what the flow needs answered before it can continue.
   * @param copy - the locale copy for this attempt.
   * @returns the chosen option id for a select prompt.
   */
  private async answerPrompt(
    entry: DesktopModelSigninFlow,
    prompt: AuthorizationPrompt,
    copy: ReturnType<typeof desktopModelSigninCopy>,
  ): Promise<string> {
    if (prompt.signal?.aborted) throw new AuthorizationDeclinedError()
    if (prompt.kind === 'select') return await this.answerSelect(entry, prompt, copy)
    if (prompt.signal !== undefined) {
      await this.waitForWithdrawnPrompt(entry, copy, prompt.signal)
      throw new AuthorizationDeclinedError()
    }
    await this.bootstrap.showDialog({
      title: entry.label,
      message: copy.promptUnsupportedMessage,
      detail: prompt.message,
      buttons: [copy.dismiss],
      cancelId: 0,
    })
    throw new AuthorizationDeclinedError()
  }

  /**
   * Render a select prompt as option buttons plus cancel.
   * @param entry - the flow whose attempt asked the question.
   * @param prompt - the select prompt, including its options.
   * @param copy - the locale copy for this attempt.
   * @returns the id of the chosen option.
   */
  private async answerSelect(
    entry: DesktopModelSigninFlow,
    prompt: Extract<AuthorizationPrompt, { kind: 'select' }>,
    copy: ReturnType<typeof desktopModelSigninCopy>,
  ): Promise<string> {
    if (prompt.options.length === 0 || prompt.options.length > MAX_SELECT_OPTIONS) {
      await this.bootstrap.showDialog({
        title: entry.label,
        message: copy.promptUnsupportedMessage,
        detail: prompt.message,
        buttons: [copy.dismiss],
        cancelId: 0,
      })
      throw new AuthorizationDeclinedError()
    }
    const descriptions = prompt.options
      .map(option => option.description)
      .filter((text): text is string => typeof text === 'string' && text.length > 0)
    const response = await this.bootstrap.showDialog({
      title: entry.label,
      message: prompt.message,
      ...(descriptions.length === 0 ? {} : { detail: descriptions.join('\n') }),
      buttons: [...prompt.options.map(option => option.label), copy.cancelLogin],
      defaultId: 0,
      cancelId: prompt.options.length,
      ...(prompt.signal === undefined ? {} : { signal: prompt.signal }),
    })
    const option = prompt.options[response]
    if (option === undefined) throw new AuthorizationDeclinedError()
    return option.id
  }

  /**
   * Hold a raced typed-code prompt until the browser callback withdraws it,
   * or until the human cancels.
   * @param entry - the flow whose attempt is waiting.
   * @param copy - the locale copy for this attempt.
   * @param signal - the per-prompt withdrawal signal.
   */
  private async waitForWithdrawnPrompt(
    entry: DesktopModelSigninFlow,
    copy: ReturnType<typeof desktopModelSigninCopy>,
    signal: AbortSignal,
  ): Promise<void> {
    try {
      await this.bootstrap.showDialog({
        title: entry.label,
        message: copy.browserWaitMessage,
        advisory: copy.deviceAdvisory,
        buttons: [copy.cancelLogin],
        cancelId: 0,
        signal,
      })
    } catch {
      /* a broken waiting dialog must not leave the prompt hanging */
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
