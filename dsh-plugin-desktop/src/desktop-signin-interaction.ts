/** Shared sign-in interaction plumbing for the tray relay and the account library. */

import { type Context } from '@deepseek-ai/cordis'
import { AuthorizationDeclinedError } from '@deepseek-ai/dsh-authorization'
import type {
  AuthorizationInteraction,
  AuthorizationNotice,
  AuthorizationPrompt,
} from '@deepseek-ai/dsh-authorization'
import { credentialKeyId } from '@deepseek-ai/dsh-credentials'
import type { CredentialKey } from '@deepseek-ai/dsh-credentials'
import type { DesktopModelSigninBootstrap, DesktopModelSigninDialog, DesktopModelSigninFlow } from './desktop-model-signin.ts'
import { desktopModelSigninCopy } from './model-signin-locale.ts'

/** Native dialogs allow 4 buttons; one is reserved for cancel. */
const MAX_SELECT_OPTIONS = 3

/**
 * The credential scope the shipped LLM adapter family owns. Both sign-in
 * surfaces write their commits here, and the Models-page profile row lives
 * under the same name in settings.
 */
export const LLM_SIGNIN_SCOPE = 'llm-pi-ai'

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

/**
 * Write an empty catalog profile for a provider that just signed in, so the
 * Models page and session picker can use the stored credential. Login itself
 * only writes `llm-pi-ai/<id>`; without a `providers.<id>` profile the route
 * never appears. An existing profile is left untouched. A settings write
 * failure is logged and does not retract the saved credential.
 * @param ctx - the context carrying the optional settings service.
 * @param key - the credential key whose id is the catalog provider route.
 * @returns once the profile row is present or the failure was logged.
 */
export async function ensureSignedInProviderRoute(ctx: Context, key: CredentialKey): Promise<void> {
  const settings = ctx.get('settings')
  if (settings === undefined) return
  const providerId = credentialKeyId(key)
  if (hasConfiguredProvider(settings.get(LLM_SIGNIN_SCOPE), providerId)) return
  try {
    await settings.update(LLM_SIGNIN_SCOPE, { providers: { [providerId]: {} } })
  } catch (cause) {
    ctx.logger.warn(
      `dsh-plugin-desktop: signed-in provider "${providerId}" is stored, but adding its Models route failed`,
    )
    ctx.logger.warn(cause)
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
 * @param ctx - the context whose authorization service owns the attempt.
 * @param bootstrap - the native capabilities the dialogs render through.
 * @param entry - the flow whose attempt is running.
 * @returns the interaction to hand `authorization.begin`.
 */
export function buildSigninInteraction(
  ctx: Context,
  bootstrap: DesktopModelSigninBootstrap,
  entry: DesktopModelSigninFlow,
): AuthorizationInteraction {
  const copy = desktopModelSigninCopy(bootstrap.locale())
  return {
    notify: (notice: AuthorizationNotice): void => {
      if (notice.url !== undefined) bootstrap.openExternal(notice.url)
      if (notice.code === undefined) return
      void bootstrap.showDialog({
        title: entry.label,
        message: notice.message.length > 0 ? notice.message : copy.deviceMessage,
        detail: copy.deviceCodeDetail(notice.code),
        advisory: copy.deviceAdvisory,
        buttons: [copy.dismiss, copy.cancelLogin],
        defaultId: 0,
        cancelId: 1,
      }).then(response => {
        if (response === 1) ctx.get('authorization')?.cancel(entry.key)
      }).catch(() => { /* a dismissed dialog must not fail the attempt it describes */ })
    },
    prompt: prompt => answerPrompt(bootstrap, entry, prompt, copy),
  }
}

/**
 * Answer one authorization prompt through native dialogs, or decline it.
 * @param bootstrap - the native capabilities the dialogs render through.
 * @param entry - the flow whose attempt asked the question.
 * @param prompt - what the flow needs answered before it can continue.
 * @param copy - the locale copy for this attempt.
 * @returns the chosen option id for a select prompt.
 */
async function answerPrompt(
  bootstrap: DesktopModelSigninBootstrap,
  entry: DesktopModelSigninFlow,
  prompt: AuthorizationPrompt,
  copy: ReturnType<typeof desktopModelSigninCopy>,
): Promise<string> {
  if (prompt.signal?.aborted) throw new AuthorizationDeclinedError()
  if (prompt.kind === 'select') return await answerSelect(bootstrap, entry, prompt, copy)
  if (prompt.signal !== undefined) {
    await waitForWithdrawnPrompt(bootstrap, entry, copy, prompt.signal)
    throw new AuthorizationDeclinedError()
  }
  await bootstrap.showDialog({
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
 * @param bootstrap - the native capabilities the dialogs render through.
 * @param entry - the flow whose attempt asked the question.
 * @param prompt - the select prompt, including its options.
 * @param copy - the locale copy for this attempt.
 * @returns the id of the chosen option.
 */
async function answerSelect(
  bootstrap: DesktopModelSigninBootstrap,
  entry: DesktopModelSigninFlow,
  prompt: Extract<AuthorizationPrompt, { kind: 'select' }>,
  copy: ReturnType<typeof desktopModelSigninCopy>,
): Promise<string> {
  if (prompt.options.length === 0 || prompt.options.length > MAX_SELECT_OPTIONS) {
    await bootstrap.showDialog({
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
  const response = await bootstrap.showDialog({
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
 * @param bootstrap - the native capabilities the dialogs render through.
 * @param entry - the flow whose attempt is waiting.
 * @param copy - the locale copy for this attempt.
 * @param signal - the per-prompt withdrawal signal.
 */
async function waitForWithdrawnPrompt(
  bootstrap: DesktopModelSigninBootstrap,
  entry: DesktopModelSigninFlow,
  copy: ReturnType<typeof desktopModelSigninCopy>,
  signal: AbortSignal,
): Promise<void> {
  try {
    await bootstrap.showDialog({
      title: entry.label,
      message: copy.browserWaitMessage,
      advisory: copy.deviceAdvisory,
      buttons: [copy.cancelLogin],
      cancelId: 0,
      signal,
    } satisfies DesktopModelSigninDialog)
  } catch {
    /* a broken waiting dialog must not leave the prompt hanging */
  }
}
