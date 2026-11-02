/**
 * Native multi-account library for the OAuth model providers the sign-in
 * relay lists: the provider's active credential keeps its upstream slot while
 * further accounts wait in desktop-owned slots, and switching swaps the two.
 */

import { type Context, Service } from '@deepseek-ai/cordis'
import { AuthorizationError } from '@deepseek-ai/dsh-authorization'
import { credentialKey, credentialKeyId, credentialKeyScope } from '@deepseek-ai/dsh-credentials'
import type { CredentialKey, CredentialProvider, CredentialRecord } from '@deepseek-ai/dsh-credentials'
import type { DesktopAccountsProviderView, DesktopModelAccountView } from './desktop-settings-contract.ts'
import { desktopModelAccountCopy } from './desktop-accounts-locale.ts'
import type { DesktopModelSigninBootstrap, DesktopModelSigninFlow } from './desktop-model-signin.ts'
import { buildSigninInteraction, ensureSignedInProviderRoute, LLM_SIGNIN_SCOPE } from './desktop-signin-interaction.ts'

/**
 * The record scope the account library owns. Saved accounts are verbatim
 * `llm-pi-ai` grant records parked under this desktop-owned scope, so the LLM
 * adapter's own record enumeration never sees them as provider credentials.
 */
export const MODEL_ACCOUNT_SCOPE = 'dsh-desktop'

/** Suffix that turns a provider id into one saved account's record id. */
const SAVED_ID_INFIX = '-acct-'

/** The method the account library can run, matching the sign-in relay. */
const NATIVE_METHOD = 'oauth'

/**
 * The active credential slot of one provider, owned by the LLM adapter.
 * @param providerId - the catalog provider route, such as `xai`.
 * @returns the credential key every request reads.
 */
export function activeAccountKey(providerId: string): CredentialKey {
  return credentialKey(LLM_SIGNIN_SCOPE, providerId)
}

/**
 * One saved (not active) account's slot.
 * @param providerId - the catalog provider route the account belongs to.
 * @param n - the 1-based slot number; gaps are reusable.
 * @returns the desktop-owned credential key for the slot.
 */
export function savedAccountKey(providerId: string, n: number): CredentialKey {
  return credentialKey(MODEL_ACCOUNT_SCOPE, `${providerId}${SAVED_ID_INFIX}${String(n)}`)
}

/**
 * Split one saved slot id back into its provider and slot number.
 * @param id - a record id under {@link MODEL_ACCOUNT_SCOPE}.
 * @returns the provider id and slot number, or `undefined` for foreign ids.
 */
export function parseSavedAccountId(id: string): { readonly providerId: string, readonly n: number } | undefined {
  const match = new RegExp(`^(.+)${SAVED_ID_INFIX}(\\d+)$`, 'u').exec(id)
  if (match === null) return undefined
  const providerId = match[1]
  const n = Number(match[2])
  if (providerId === undefined || providerId.length === 0 || !Number.isSafeInteger(n) || n < 1) return undefined
  return { providerId, n }
}

/** Raised when a mutation names a provider or saved slot that does not exist. */
export class DesktopAccountUnknownError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'DesktopAccountUnknownError'
  }
}

/**
 * Read the catalog provider routes configured on the Models page from the
 * `llm-pi-ai` settings namespace. Missing service or namespace yields none.
 * @param ctx - the context carrying the optional settings service.
 * @returns the configured provider route ids.
 */
function configuredProviderIds(ctx: Context): readonly string[] {
  const settings = ctx.get('settings')
  if (settings === undefined) return []
  const section = settings.get(LLM_SIGNIN_SCOPE)
  if (section === null || typeof section !== 'object' || Array.isArray(section)) return []
  const providers = (section as { providers?: unknown }).providers
  if (providers === null || typeof providers !== 'object' || Array.isArray(providers)) return []
  return Object.keys(providers)
}

/** Decode non-secret identity claims embedded in an OAuth access JWT. */
function oauthIdentity(access: unknown): string | undefined {
  if (typeof access !== 'string') return undefined
  const parts = access.split('.')
  if (parts.length !== 3 || parts[1] === undefined) return undefined
  try {
    const claims = JSON.parse(Buffer.from(parts[1], 'base64url').toString('utf8')) as Record<string, unknown>
    const profile = claims['https://api.openai.com/profile']
    if (profile !== null && typeof profile === 'object' && !Array.isArray(profile)) {
      const name = (profile as { name?: unknown }).name
      const email = (profile as { email?: unknown }).email
      if (typeof name === 'string' && name.length > 0 && typeof email === 'string' && email.length > 0) {
        return `${name} <${email}>`.slice(0, 200)
      }
      if (typeof email === 'string' && email.length > 0) return email.slice(0, 200)
      if (typeof name === 'string' && name.length > 0) return name.slice(0, 200)
    }
    const principalId = claims.principal_id
    return typeof principalId === 'string' && principalId.length > 0 ? principalId.slice(0, 200) : undefined
  } catch {
    // A malformed or opaque access token carries no usable display identity.
    return undefined
  }
}

/**
 * Derive a recognizable display label from one stored grant without exposing
 * access or refresh tokens. Embedded profile name/email wins over opaque ids.
 * @param record - the stored record, if any.
 * @param fallback - the label to use when no identity is recoverable.
 * @returns the user-facing account label.
 */
function accountLabel(record: CredentialRecord | undefined, fallback: string): string {
  if (record?.kind !== 'grant' || typeof record.payload !== 'object' || record.payload === null) return fallback
  const payload = record.payload as { access?: unknown, accountId?: unknown }
  const identity = oauthIdentity(payload.access)
  if (identity !== undefined) return identity
  return typeof payload.accountId === 'string' && payload.accountId.length > 0 ? payload.accountId : fallback
}

/**
 * Read the active and saved accounts of every model provider and keep the
 * answer cached for the synchronous tray submenu. Mutations and credential
 * record events recompute the cache; every write goes through the credential
 * seam's serialized `modifyRecord`, so a concurrent token refresh can never
 * interleave with a swap.
 */
export class DesktopModelAccountService extends Service {
  /** Last computed provider views; the tray reads this synchronously. */
  private snapshot: readonly DesktopAccountsProviderView[] = []

  constructor(ctx: Context, private readonly bootstrap: DesktopModelSigninBootstrap) {
    super(ctx, 'desktopModelAccounts')
    const listeners = [
      ctx.on('credentials/record-updated', () => { this.recompute() }),
      ctx.on('authorization/settled', () => { this.recompute() }),
    ]
    ctx.effect(() => () => {
      for (const dispose of listeners.splice(0)) dispose()
    }, 'dsh-plugin-desktop: model account listeners')
    this.recompute()
  }

  /**
   * The last computed provider views, safe to read while building the tray
   * menu. Views carry labels and ids only, never credential payloads.
   * @returns the cached provider views.
   */
  snapshotOf(): readonly DesktopAccountsProviderView[] {
    return this.snapshot
  }

  /** Recompute the cached provider views without blocking the caller. */
  private recompute(): void {
    void this.list().then((providers) => { this.snapshot = providers }).catch((cause: unknown) => {
      this.ctx.logger.warn('dsh-plugin-desktop: model account enumeration failed')
      this.ctx.logger.warn(cause)
    })
  }

  /**
   * Enumerate every provider with an active or saved model account.
   * @returns one view per provider, labels and ids only.
   */
  async list(): Promise<readonly DesktopAccountsProviderView[]> {
    const startedAt = Date.now()
    const stageMarks: string[] = []
    const mark = (stage: string): void => {
      stageMarks.push(`${stage}@${Date.now() - startedAt}ms`)
    }
    const credentials = this.ctx.get('credentials')
    const authorization = this.ctx.get('authorization')
    mark('services-resolved')
    // Authorization also advertises API-key-only providers. This page owns
    // OAuth account libraries, so those rows are not manageable accounts.
    const oauthLabels = new Map<string, string>()
    for (const entry of authorization?.list() ?? []) {
      if (credentialKeyScope(entry.key) !== LLM_SIGNIN_SCOPE) continue
      if (!entry.methods.some(method => method.id === NATIVE_METHOD)) continue
      oauthLabels.set(credentialKeyId(entry.key), entry.label)
    }
    mark('authorization-listed')
    const configured = new Set<string>(configuredProviderIds(this.ctx))
    const providerIds = new Set<string>(
      [...configured].filter(providerId => oauthLabels.has(providerId)),
    )
    const savedNumbers = new Map<string, number[]>()
    const saved = new Map<string, CredentialRecord | undefined>()
    if (credentials !== undefined) {
      for (const record of await credentials.listRecords()) {
        mark('record-listed')
        const id = credentialKeyId(record.key)
        const scope = credentialKeyScope(record.key)
        if (scope === LLM_SIGNIN_SCOPE) {
          // Existing OAuth grants remain manageable even when an optional
          // authorization provider is temporarily unavailable.
          if (record.kind === 'grant') providerIds.add(id)
          continue
        }
        if (scope !== MODEL_ACCOUNT_SCOPE) continue
        const parsed = parseSavedAccountId(id)
        if (parsed === undefined) continue
        providerIds.add(parsed.providerId)
        saved.set(`${parsed.providerId}:${String(parsed.n)}`, await credentials.readRecord(record.key))
        const numbers = savedNumbers.get(parsed.providerId) ?? []
        if (!numbers.includes(parsed.n)) numbers.push(parsed.n)
        savedNumbers.set(parsed.providerId, numbers)
      }
    }
    const providers = new Map<string, { label: string, savedNumbers: number[] }>()
    for (const providerId of providerIds) {
      providers.set(providerId, {
        label: oauthLabels.get(providerId) ?? providerId,
        savedNumbers: savedNumbers.get(providerId) ?? [],
      })
    }
    const views: DesktopAccountsProviderView[] = []
    const copy = desktopModelAccountCopy(this.bootstrap.locale())
    for (const [providerId, { label, savedNumbers }] of [...providers].sort((left, right) => left[0].localeCompare(right[0]))) {
      const activeRecord = credentials === undefined
        ? undefined
        : await credentials.readRecord(activeAccountKey(providerId))
      mark(`active-read:${providerId}`)
      const activeLabel = activeRecord === undefined
        ? null
        : accountLabel(activeRecord, copy.activeFallback)
      const accounts: DesktopModelAccountView[] = savedNumbers.sort((left, right) => left - right)
        .map(n => ({
          id: `${providerId}${SAVED_ID_INFIX}${String(n)}`,
          label: accountLabel(saved.get(`${providerId}:${String(n)}`), copy.savedFallback(n)),
        }))
      views.push({
        provider: providerId,
        label,
        activeLabel,
        inFlight: authorization?.describe(activeAccountKey(providerId))?.inFlight === true,
        saved: accounts,
      })
    }
    // Slow listings correlate with a credential-store lock held by a stuck
    // OAuth refresh; the stage marks name the first await that stalled.
    const elapsedMs = Date.now() - startedAt
    if (elapsedMs > 1_000) {
      this.ctx.logger.warn(
        `dsh-plugin-desktop: model account listing took ${elapsedMs}ms (stages: ${stageMarks.join(', ')})`,
      )
    }
    return views
  }

  /**
   * Sign one more account in without displacing the active one: snapshot the
   * active credential, run the OAuth attempt through the sign-in interaction,
   * then shelve the new grant into a free library slot and restore the
   * snapshot. The first account becomes active directly.
   * @param providerId - the catalog provider route to sign in.
   * @returns once the attempt settled and its outcome dialog was dismissed.
   */
  async add(providerId: string): Promise<void> {
    const copy = desktopModelAccountCopy(this.bootstrap.locale())
    const authorization = this.ctx.get('authorization')
    const credentials = this.ctx.get('credentials')
    if (authorization === undefined || credentials === undefined) {
      await this.show(copy.unavailableTitle, copy.unavailableMessage)
      return
    }
    const key = activeAccountKey(providerId)
    const entry = authorization.describe(key)
    if (entry === undefined || !entry.methods.some(method => method.id === NATIVE_METHOD)) {
      await this.show(copy.unavailableTitle, copy.unavailableMessage)
      return
    }
    const flow: DesktopModelSigninFlow = {
      key,
      label: entry.label,
      methods: entry.methods,
      inFlight: entry.inFlight,
    }
    try {
      const before = await credentials.readRecord(key)
      const outcome = await authorization.begin({
        key,
        method: NATIVE_METHOD,
        interaction: buildSigninInteraction(this.ctx, this.bootstrap, flow),
      })
      if (outcome.status !== 'authorized') return
      const fresh = await credentials.readRecord(key)
      if (fresh === undefined) {
        throw new Error(`the signed-in credential for "${providerId}" disappeared before it was shelved`)
      }
      if (before === undefined) {
        await this.show(entry.label, copy.addedActiveMessage)
      } else {
        const slot = savedAccountKey(providerId, await this.nextSavedNumber(providerId))
        await credentials.modifyRecord(slot, async () => fresh)
        await credentials.modifyRecord(key, async () => before)
        await this.show(entry.label, copy.addedKeptActiveMessage)
      }
      await ensureSignedInProviderRoute(this.ctx, key)
    } catch (cause) {
      await this.reportFailure(entry, cause, copy)
    }
  }

  /**
   * Start one add attempt without blocking the caller; the web settings page
   * starts the native flow this way and polls the list view for the outcome.
   * @param providerId - the catalog provider route to sign in.
   */
  startAdd(providerId: string): void {
    void this.add(providerId).catch((cause: unknown) => {
      this.ctx.logger.warn(`dsh-plugin-desktop: background account sign-in for "${providerId}" failed`)
      this.ctx.logger.warn(cause)
    })
  }

  /**
   * Make one saved account the active credential, demoting the previous one
   * into the freed slot. The next model request reads the new credential.
   * @param providerId - the catalog provider route whose account changes.
   * @param slotId - the saved slot id to activate, such as `xai-acct-1`.
   * @returns the label the activated account displays.
   */
  async switchTo(providerId: string, slotId: string): Promise<string> {
    const credentials = this.requireCredentials()
    const parsed = parseSavedAccountId(slotId)
    if (parsed?.providerId !== providerId) {
      throw new DesktopAccountUnknownError(`no saved model account "${slotId}" exists for "${providerId}"`)
    }
    const slot = savedAccountKey(parsed.providerId, parsed.n)
    const incoming = await credentials.readRecord(slot)
    if (incoming === undefined) {
      throw new DesktopAccountUnknownError(`no saved model account "${slotId}" exists for "${providerId}"`)
    }
    const previous = await credentials.readRecord(activeAccountKey(providerId))
    await credentials.modifyRecord(activeAccountKey(providerId), async () => incoming)
    if (previous === undefined) await credentials.deleteRecord(slot)
    else await credentials.modifyRecord(slot, async () => previous)
    return accountLabel(incoming, desktopModelAccountCopy(this.bootstrap.locale()).savedFallback(parsed.n))
  }

  /**
   * Delete one saved account from the library; the active credential stays.
   * @param providerId - the catalog provider route the account belongs to.
   * @param slotId - the saved slot id to delete.
   */
  async remove(providerId: string, slotId: string): Promise<string> {
    const credentials = this.requireCredentials()
    const parsed = parseSavedAccountId(slotId)
    if (parsed?.providerId !== providerId) {
      throw new DesktopAccountUnknownError(`no saved model account "${slotId}" exists for "${providerId}"`)
    }
    const slot = savedAccountKey(parsed.providerId, parsed.n)
    const stored = await credentials.readRecord(slot)
    if (stored === undefined) {
      throw new DesktopAccountUnknownError(`no saved model account "${slotId}" exists for "${providerId}"`)
    }
    await credentials.deleteRecord(slot)
    return accountLabel(stored, desktopModelAccountCopy(this.bootstrap.locale()).savedFallback(parsed.n))
  }

  /**
   * Delete the active credential of one provider. Saved accounts stay in the
   * library; the next request fails until another account is activated.
   * @param providerId - the catalog provider route to sign out.
   */
  async logout(providerId: string): Promise<void> {
    const credentials = this.requireCredentials()
    await credentials.deleteRecord(activeAccountKey(providerId))
  }

  /** @returns the credential service, or throws when the profile mounts none. */
  private requireCredentials(): CredentialProvider {
    const credentials = this.ctx.get('credentials')
    if (credentials === undefined) {
      throw new Error('dsh-plugin-desktop: this composition mounts no credentials service, so model accounts are unavailable')
    }
    return credentials
  }

  /**
   * Find the lowest free slot number for one provider's library.
   * @param providerId - the catalog provider route to scan.
   * @returns one past the highest stored slot number.
   */
  private async nextSavedNumber(providerId: string): Promise<number> {
    const credentials = this.ctx.get('credentials')
    if (credentials === undefined) return 1
    let max = 0
    for (const record of await credentials.listRecords()) {
      if (credentialKeyScope(record.key) !== MODEL_ACCOUNT_SCOPE) continue
      const parsed = parseSavedAccountId(credentialKeyId(record.key))
      if (parsed?.providerId === providerId) max = Math.max(max, parsed.n)
    }
    return max + 1
  }

  /** Raise one information dialog through the injected native bootstrap. */
  private async show(title: string, message: string): Promise<void> {
    const copy = desktopModelAccountCopy(this.bootstrap.locale())
    await this.bootstrap.showDialog({ title, message, buttons: [copy.dismiss], cancelId: 0 })
  }

  /** Map one failed attempt onto the copy its failure code names. */
  private async reportFailure(
    entry: { readonly label: string },
    cause: unknown,
    copy: ReturnType<typeof desktopModelAccountCopy>,
  ): Promise<void> {
    if (cause instanceof AuthorizationError && cause.code === 'ALREADY_IN_FLIGHT') {
      await this.show(entry.label, copy.inFlightMessage)
      return
    }
    const detail = cause instanceof Error ? cause.message : String(cause)
    await this.show(entry.label, copy.failedMessage(detail))
  }
}

declare module '@deepseek-ai/cordis' {
  interface Context {
    /** Tray- and settings-facing multi-account library for model providers. */
    desktopModelAccounts: DesktopModelAccountService
  }
}

export default DesktopModelAccountService
