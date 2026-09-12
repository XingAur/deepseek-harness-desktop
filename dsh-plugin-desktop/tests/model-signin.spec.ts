import { Context } from '@deepseek-ai/cordis'
import { AuthorizationDeclinedError, AuthorizationError } from '@deepseek-ai/dsh-authorization'
import type {
  AuthorizationEntry,
  AuthorizationInteraction,
  AuthorizationRequest,
  AuthorizationService,
} from '@deepseek-ai/dsh-authorization'
import { credentialKey, credentialKeyId } from '@deepseek-ai/dsh-credentials'
import type { SettingsProvider } from '@deepseek-ai/dsh-settings'
import { describe, expect, it, vi } from 'vitest'
import DesktopModelSigninService, {
  type DesktopModelSigninBootstrap,
  type DesktopModelSigninDialog,
} from '../src/desktop-model-signin.ts'

const XAI_KEY = credentialKey('llm-pi-ai', 'xai')

/** Structurally compatible stand-in for the optional authorization service. */
class FakeAuthorization {
  readonly requests: AuthorizationRequest[] = []
  outcome: 'authorized' | 'cancelled' = 'authorized'
  failure: Error | undefined
  readonly cancelledKeys: string[] = []

  constructor(readonly entries: readonly AuthorizationEntry[] = []) {}

  list(): readonly AuthorizationEntry[] {
    return this.entries
  }

  async begin(request: AuthorizationRequest): Promise<{ readonly status: string }> {
    this.requests.push(request)
    if (this.failure !== undefined) throw this.failure
    return { status: this.outcome }
  }

  cancel(key: Parameters<AuthorizationService['cancel']>[0]): void {
    this.cancelledKeys.push(credentialKeyId(key))
  }
}

function xaiEntry(): AuthorizationEntry {
  return {
    key: XAI_KEY,
    label: 'xAI',
    methods: [{ id: 'oauth', label: 'Sign in with xAI' }],
    inFlight: false,
  }
}

/** Structurally compatible stand-in for the optional settings service. */
class FakeSettings {
  section: unknown = {}
  readonly updates: object[] = []
  failure: Error | undefined

  get(ns: string): unknown {
    return ns === 'llm-pi-ai' ? this.section : undefined
  }

  async update(ns: string, patch: object): Promise<void> {
    if (ns !== 'llm-pi-ai') throw new Error(`unexpected namespace ${ns}`)
    if (this.failure !== undefined) throw this.failure
    this.updates.push(patch)
  }
}

function bootstrap(dialogs: DesktopModelSigninDialog[]): DesktopModelSigninBootstrap {
  return {
    openExternal: vi.fn(),
    showDialog: async spec => {
      dialogs.push(spec)
      return 0
    },
    locale: () => 'zh',
  }
}

async function mount(
  authorization: FakeAuthorization | undefined,
  boot: DesktopModelSigninBootstrap,
  settings?: FakeSettings,
): Promise<DesktopModelSigninService> {
  const ctx = new Context()
  if (authorization !== undefined) {
    ctx.provide('authorization', authorization as unknown as AuthorizationService)
  }
  if (settings !== undefined) {
    ctx.provide('settings', settings as unknown as SettingsProvider)
  }
  const fiber = ctx.plugin(DesktopModelSigninService, boot)
  await fiber
  return ctx.desktopModelSignin
}

describe('desktop model sign-in relay', () => {
  it('lists nothing when no authorization capability is mounted', async () => {
    const service = await mount(undefined, bootstrap([]))
    expect(service.flows()).toEqual([])
  })

  it('lists only the LLM adapter scope and carries method choices', async () => {
    const other = credentialKey('elsewhere', 'thing')
    const authorization = new FakeAuthorization([
      xaiEntry(),
      { key: other, label: 'Other', methods: [{ id: 'oauth', label: 'x' }], inFlight: true },
    ])
    const service = await mount(authorization, bootstrap([]))
    expect(service.flows()).toEqual([
      { key: XAI_KEY, label: 'xAI', methods: [{ id: 'oauth', label: 'Sign in with xAI' }], inFlight: false },
    ])
  })

  it('relays the device-code notice to the browser and a dialog, then reports success', async () => {
    const dialogs: DesktopModelSigninDialog[] = []
    const boot = bootstrap(dialogs)
    const authorization = new FakeAuthorization([xaiEntry()])
    let interaction!: AuthorizationInteraction
    vi.spyOn(authorization, 'begin').mockImplementation(async request => {
      interaction = request.interaction
      interaction.notify({ message: 'Continue in your browser.', url: 'https://auth.x.ai/device', code: 'BZEH-ANSR' })
      return { status: 'authorized' }
    })
    const service = await mount(authorization, boot)

    await service.beginSignIn(service.flows()[0]!)

    expect(boot.openExternal).toHaveBeenCalledWith('https://auth.x.ai/device')
    const device = dialogs[0]
    expect(device).toMatchObject({
      title: 'xAI',
      detail: '确认码：BZEH-ANSR',
      buttons: ['知道了', '取消登录'],
      cancelId: 1,
    })
    expect(dialogs.at(-1)?.message).toContain('凭据已保存')
  })

  it('cancels the running attempt when the device dialog picks the cancel button', async () => {
    const dialogs: DesktopModelSigninDialog[] = []
    const boot = bootstrap(dialogs)
    const authorization = new FakeAuthorization([xaiEntry()])
    vi.spyOn(authorization, 'begin').mockImplementation(async request => {
      void request.interaction.notify({ message: 'Continue in your browser.', url: 'https://auth.x.ai/device', code: 'BZEH-ANSR' })
      return { status: 'cancelled' }
    })
    boot.showDialog = async spec => {
      dialogs.push(spec)
      return spec.buttons.length > 1 ? 1 : 0
    }
    const service = await mount(authorization, boot)

    await service.beginSignIn(service.flows()[0]!)
    await Promise.resolve()

    expect(authorization.cancelledKeys).toEqual([credentialKeyId(XAI_KEY)])
  })

  it('declines a prompt the native dialogs cannot answer, settling the attempt as cancelled', async () => {
    const dialogs: DesktopModelSigninDialog[] = []
    const authorization = new FakeAuthorization([xaiEntry()])
    let caught: unknown
    vi.spyOn(authorization, 'begin').mockImplementation(async request => {
      try {
        await request.interaction.prompt({ kind: 'text', message: 'Paste a key' })
      } catch (cause) {
        caught = cause
      }
      return { status: 'cancelled' }
    })
    const service = await mount(authorization, bootstrap(dialogs))

    await service.beginSignIn(service.flows()[0]!)

    expect(caught).toBeInstanceOf(AuthorizationDeclinedError)
    expect(dialogs[0]?.message).toContain('暂不支持')
  })

  it('answers a select prompt with the chosen option id', async () => {
    const dialogs: DesktopModelSigninDialog[] = []
    const authorization = new FakeAuthorization([xaiEntry()])
    let answer: string | undefined
    vi.spyOn(authorization, 'begin').mockImplementation(async request => {
      answer = await request.interaction.prompt({
        kind: 'select',
        message: 'Select OpenAI Codex login method:',
        options: [
          { id: 'browser', label: 'Browser login (default)' },
          { id: 'device_code', label: 'Device code login (headless)' },
        ],
      })
      return { status: 'authorized' }
    })
    const service = await mount(authorization, bootstrap(dialogs))

    await service.beginSignIn(service.flows()[0]!)

    expect(answer).toBe('browser')
    expect(dialogs[0]).toMatchObject({
      message: 'Select OpenAI Codex login method:',
      buttons: ['Browser login (default)', 'Device code login (headless)', '取消登录'],
      defaultId: 0,
      cancelId: 2,
    })
    expect(dialogs.at(-1)?.message).toContain('凭据已保存')
  })

  it('declines a select prompt when the cancel button is chosen', async () => {
    const dialogs: DesktopModelSigninDialog[] = []
    const boot = bootstrap(dialogs)
    const authorization = new FakeAuthorization([xaiEntry()])
    let caught: unknown
    vi.spyOn(authorization, 'begin').mockImplementation(async request => {
      try {
        await request.interaction.prompt({
          kind: 'select',
          message: 'Select OpenAI Codex login method:',
          options: [
            { id: 'browser', label: 'Browser login (default)' },
            { id: 'device_code', label: 'Device code login (headless)' },
          ],
        })
      } catch (cause) {
        caught = cause
      }
      return { status: 'cancelled' }
    })
    boot.showDialog = async spec => {
      dialogs.push(spec)
      return spec.cancelId ?? spec.buttons.length - 1
    }
    const service = await mount(authorization, boot)

    await service.beginSignIn(service.flows()[0]!)

    expect(caught).toBeInstanceOf(AuthorizationDeclinedError)
    expect(dialogs[0]?.buttons.at(-1)).toBe('取消登录')
  })

  it('declines a select prompt with more options than native buttons allow', async () => {
    const dialogs: DesktopModelSigninDialog[] = []
    const authorization = new FakeAuthorization([xaiEntry()])
    let caught: unknown
    vi.spyOn(authorization, 'begin').mockImplementation(async request => {
      try {
        await request.interaction.prompt({
          kind: 'select',
          message: 'Pick one',
          options: [
            { id: 'a', label: 'A' },
            { id: 'b', label: 'B' },
            { id: 'c', label: 'C' },
            { id: 'd', label: 'D' },
          ],
        })
      } catch (cause) {
        caught = cause
      }
      return { status: 'cancelled' }
    })
    const service = await mount(authorization, bootstrap(dialogs))

    await service.beginSignIn(service.flows()[0]!)

    expect(caught).toBeInstanceOf(AuthorizationDeclinedError)
    expect(dialogs[0]?.message).toContain('暂不支持')
  })

  it('waits for a raced typed-code prompt until it is withdrawn', async () => {
    const dialogs: DesktopModelSigninDialog[] = []
    const boot = bootstrap(dialogs)
    const authorization = new FakeAuthorization([xaiEntry()])
    const controller = new AbortController()
    let caught: unknown
    boot.showDialog = spec => new Promise(resolve => {
      dialogs.push(spec)
      spec.signal?.addEventListener('abort', () => resolve(spec.cancelId ?? 0), { once: true })
    })
    vi.spyOn(authorization, 'begin').mockImplementation(async request => {
      const pending = request.interaction.prompt({
        kind: 'text',
        message: 'Paste the authorization code',
        signal: controller.signal,
      })
      await vi.waitFor(() => { expect(dialogs).toHaveLength(1) })
      expect(dialogs[0]).toMatchObject({
        message: '浏览器已打开登录页面。请在页面中完成登录，完成后会自动继续。',
        buttons: ['取消登录'],
        cancelId: 0,
      })
      controller.abort()
      try {
        await pending
      } catch (cause) {
        caught = cause
      }
      return { status: 'cancelled' }
    })
    const service = await mount(authorization, boot)

    await service.beginSignIn(service.flows()[0]!)

    expect(caught).toBeInstanceOf(AuthorizationDeclinedError)
  })

  it('declines an already-withdrawn prompt without opening a dialog', async () => {
    const dialogs: DesktopModelSigninDialog[] = []
    const authorization = new FakeAuthorization([xaiEntry()])
    const controller = new AbortController()
    controller.abort()
    let caught: unknown
    vi.spyOn(authorization, 'begin').mockImplementation(async request => {
      try {
        await request.interaction.prompt({
          kind: 'text',
          message: 'Paste the authorization code',
          signal: controller.signal,
        })
      } catch (cause) {
        caught = cause
      }
      return { status: 'cancelled' }
    })
    const service = await mount(authorization, bootstrap(dialogs))

    await service.beginSignIn(service.flows()[0]!)

    expect(caught).toBeInstanceOf(AuthorizationDeclinedError)
    expect(dialogs).toEqual([])
  })

  it('names an already-running attempt instead of surfacing it as a failure', async () => {
    const dialogs: DesktopModelSigninDialog[] = []
    const authorization = new FakeAuthorization([xaiEntry()])
    authorization.failure = new AuthorizationError('busy', 'ALREADY_IN_FLIGHT')
    const service = await mount(authorization, bootstrap(dialogs))

    await service.beginSignIn(service.flows()[0]!)

    expect(dialogs[0]?.message).toContain('已有登录流程在进行中')
  })

  it('reports any other failure with its message', async () => {
    const dialogs: DesktopModelSigninDialog[] = []
    const authorization = new FakeAuthorization([xaiEntry()])
    authorization.failure = new Error('the endpoint refused')
    const service = await mount(authorization, bootstrap(dialogs))

    await service.beginSignIn(service.flows()[0]!)

    expect(dialogs[0]?.message).toBe('登录失败：the endpoint refused')
  })

  it('offers the no-method dialog and skips the seam when oauth is absent', async () => {
    const dialogs: DesktopModelSigninDialog[] = []
    const authorization = new FakeAuthorization([{
      key: XAI_KEY,
      label: 'xAI',
      methods: [{ id: 'api-key', label: 'Paste a key' }],
      inFlight: false,
    }])
    const begin = vi.spyOn(authorization, 'begin')
    const service = await mount(authorization, bootstrap(dialogs))

    await service.beginSignIn(service.flows()[0]!)

    expect(begin).not.toHaveBeenCalled()
    expect(dialogs[0]?.message).toContain('没有可用的登录方式')
  })

  it('explains the missing authorization capability instead of failing', async () => {
    const dialogs: DesktopModelSigninDialog[] = []
    const service = await mount(undefined, bootstrap(dialogs))

    await service.beginSignIn({ key: XAI_KEY, label: 'xAI', methods: [{ id: 'oauth', label: 'x' }], inFlight: false })

    expect(dialogs[0]?.message).toContain('未挂载模型授权能力')
  })

  it('adds a missing catalog profile after a successful sign-in so Models can list the provider', async () => {
    const settings = new FakeSettings()
    settings.section = { providers: { zai: {} } }
    const service = await mount(new FakeAuthorization([xaiEntry()]), bootstrap([]), settings)

    await service.beginSignIn(service.flows()[0]!)

    expect(settings.updates).toEqual([{ providers: { xai: {} } }])
  })

  it('leaves an already-configured provider profile untouched after sign-in', async () => {
    const settings = new FakeSettings()
    settings.section = { providers: { xai: { displayName: 'keep' } } }
    const service = await mount(new FakeAuthorization([xaiEntry()]), bootstrap([]), settings)

    await service.beginSignIn(service.flows()[0]!)

    expect(settings.updates).toEqual([])
  })

  it('still reports the saved credential when adding the Models route fails', async () => {
    const dialogs: DesktopModelSigninDialog[] = []
    const settings = new FakeSettings()
    settings.failure = new Error('settings namespace "llm-pi-ai" is not registered')
    const service = await mount(new FakeAuthorization([xaiEntry()]), bootstrap(dialogs), settings)

    await service.beginSignIn(service.flows()[0]!)

    expect(settings.updates).toEqual([])
    expect(dialogs.at(-1)?.message).toContain('凭据已保存')
  })
})
