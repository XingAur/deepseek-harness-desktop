import { existsSync } from 'node:fs'
import { isAbsolute, relative, resolve } from 'node:path'
import '@wdio/tauri-service'
import {
  cleanupWdioSession,
  createTauriCapabilities,
  startWdioSession,
  type TauriCapabilities,
} from '@wdio/tauri-service'

export interface FixtureRequest {
  method: string
  path: string
  range?: string
  at?: string
}

export type ProjectCoverToken = 'aurora-blue' | 'sunset' | 'forest' | 'graphite' | 'violet'

export interface DesktopHarness {
  launch(appBinary?: string): Promise<void>
  waitForPhase(phase: string, timeoutMs?: number): Promise<void>
  waitForWorkbench(timeoutMs?: number): Promise<void>
  runtimePid(): Promise<number>
  runtimePort(): Promise<number>
  requestLog(): Promise<readonly FixtureRequest[]>
  waitForWorkbenchText(text: string, timeoutMs?: number): Promise<void>
  createProfile(input: { name: string; dataRoot: string }): Promise<string>
  createProject(input: { idea: string }): Promise<void>
  selectProject(title: string): Promise<void>
  openProject(title: string): Promise<void>
  launchLocalApp(title: string): Promise<void>
  returnToWorkbenchFromApp(): Promise<void>
  stopLocalAppFromCard(title: string): Promise<void>
  renameProject(from: string, to: string): Promise<void>
  setProjectCover(title: string, cover: ProjectCoverToken): Promise<void>
  pinProject(title: string): Promise<void>
  removeProject(title: string, scope: 'unregister' | 'recycle'): Promise<void>
  quit(): Promise<void>
  exportDiagnostics(): Promise<string>
  fixturePath(name: string): string
}

export class PackagedDesktopHarness implements DesktopHarness {
  private session?: WebdriverIO.Browser
  private ownsSession = false

  constructor(session?: WebdriverIO.Browser) {
    this.session = session
  }

  async launch(appBinary = process.env.DSH_E2E_APP_BINARY): Promise<void> {
    if (appBinary === undefined || !isAbsolute(appBinary) || !existsSync(appBinary)) {
      throw new Error('E2E 应用路径无效')
    }
    if (this.session === undefined) {
      const capabilities = createTauriCapabilities(appBinary, {
        driverProvider: 'embedded',
        logLevel: 'error',
        startTimeout: 120_000,
        commandTimeout: 120_000,
      }) as TauriCapabilities
      capabilities['wdio:tauriServiceOptions'] = {
        ...capabilities['wdio:tauriServiceOptions'],
        captureBackendLogs: true,
        captureFrontendLogs: true,
        logDir: resolve(process.env.DSH_E2E_ARTIFACTS ?? 'e2e-artifacts'),
        env: e2eEnvironment(),
      }
      this.session = await startWdioSession(capabilities)
      this.ownsSession = true
    }
    const session = this.requireSession()
    await session.waitUntil(async () => (await session.getWindowHandles()).length > 0, {
      timeout: 120_000,
      timeoutMsg: '候选应用未创建 Webdriver 窗口',
    })
  }

  async waitForPhase(phase: string, timeoutMs = 120_000): Promise<void> {
    const session = this.requireSession()
    await session.waitUntil(async () => {
      const body = await session.$('body').getText()
      return body.includes(phase) || await session.$(`[data-phase="${phase}"]`).isExisting()
    }, { timeout: timeoutMs, timeoutMsg: `未进入阶段：${phase}` })
  }

  async waitForWorkbench(timeoutMs = 120_000): Promise<void> {
    const session = this.requireSession()
    await session.waitUntil(async () => {
      const frame = await session.$('iframe[title="DeepSeek Harness 工作台"]')
      return await frame.isExisting() && await frame.isDisplayed()
    }, { timeout: timeoutMs, timeoutMsg: 'DeepSeek Harness 工作台未就绪' })
  }

  async runtimePid(): Promise<number> {
    const value = await this.requireSession().execute(() => document.body.dataset.runtimePid)
    const pid = Number(value)
    if (!Number.isSafeInteger(pid) || pid <= 0) throw new Error('页面未暴露有效的测试 Runtime PID')
    return pid
  }

  async runtimePort(): Promise<number> {
    const source = await this.requireSession().$('iframe[title="DeepSeek Harness 工作台"]').getAttribute('src')
    if (source === null) throw new Error('工作台 iframe 缺少地址')
    const port = Number(new URL(source).port)
    if (!Number.isSafeInteger(port) || port <= 0) throw new Error('工作台端口无效')
    return port
  }

  async requestLog(): Promise<readonly FixtureRequest[]> {
    const endpoint = process.env.DSH_E2E_RUNTIME_FIXTURE
    if (endpoint === undefined) return []
    const response = await fetch(new URL('/__e2e/requests', endpoint))
    if (!response.ok) throw new Error(`Fixture request log 返回 ${response.status}`)
    return await response.json() as FixtureRequest[]
  }

  async waitForWorkbenchText(text: string, timeoutMs = 60_000): Promise<void> {
    await this.withWorkbenchTarget(async (page) => {
      await page.waitFor(`document.body?.innerText.includes(${JSON.stringify(text)}) === true`, {
        timeoutMs,
        message: `工作台未显示文本：${text}`,
      })
    })
  }

  async createProfile(input: { name: string; dataRoot: string }): Promise<string> {
    const session = this.requireSession()
    await session.$('button=设置').click()
    await session.$('button=Profiles').click()
    await session.$('button=新建 Profile').click()
    await session.$('input[aria-label="名称"]').setValue(input.name)
    await session.$('input[aria-label="数据目录"]').setValue(input.dataRoot)
    await session.$('button=保存').click()
    await session.waitUntil(async () => {
      for (const card of await session.$$('article')) {
        if ((await card.getText()).includes(input.name)) return true
      }
      return false
    })
    return input.name
  }

  async createProject(input: { idea: string }): Promise<void> {
    await this.withWorkbenchTarget(async (page) => {
      const submitBuild = async () => {
        await this.openLocalProjects(page)
        await page.setValue('textarea[aria-label="项目需求"]', input.idea)
        await page.clickText('检查并预览')
        await page.clickText('确认并开始构建')
      }

      await submitBuild()
      const continueButton = buttonTextExpression('继续')
      const replyVisible = conversationContainsExpression('E2E_PONG')
      await page.waitFor(`(${continueButton}) !== null || (${replyVisible})`, {
        timeoutMs: 30_000,
        message: '本地项目未进入首次会话',
      })
      if (await page.evaluate<boolean>(`(${continueButton}) !== null`)) {
        await page.clickText('继续')
        // DSH 的首次使用声明会中断触发它的导航。接受声明后重新提交，
        // 此时目录可能已创建，但项目还没有注册或启动会话。
        await submitBuild()
      }
      await page.waitFor(replyVisible, {
        timeoutMs: 60_000,
        message: '本地项目会话没有收到确定性模型回复',
      })
    })
  }

  async selectProject(title: string): Promise<void> {
    await this.withWorkbenchTarget(async (page) => {
      await this.openLocalProjects(page)
      await page.projectAction(title, 'click')
      await page.waitFor(`(${projectSurfaceExpression(title)})?.getAttribute('aria-selected') === 'true'`, {
        timeoutMs: 10_000,
        message: `项目未被选中：${title}`,
      })
    })
  }

  async openProject(title: string): Promise<void> {
    await this.withWorkbenchTarget(async (page) => {
      await this.openLocalProjects(page)
      await page.projectAction(title, 'double-click')
      await page.waitFor(`document.querySelector('section[aria-label="本地项目"]') === null`, {
        timeoutMs: 30_000,
        message: `项目未启动：${title}`,
      })
    })
  }

  // 双击可运行的项目卡片启动本地应用，随后在主窗口（tauri 壳层）等待应用视图出现。
  async launchLocalApp(title: string): Promise<void> {
    await this.withWorkbenchTarget(async (page) => {
      await this.openLocalProjects(page)
      await page.projectAction(title, 'double-click')
    })
    await this.withMainWindowTarget(async (page) => {
      await page.waitFor(`document.querySelector('section[aria-label="本地应用视图"]') !== null`, {
        timeoutMs: 30_000,
        message: `本地应用视图未出现：${title}`,
      })
    })
  }

  // 在主窗口的应用视图条上点击「返回工作台」，并等待应用视图卸载、工作台重新可见。
  async returnToWorkbenchFromApp(): Promise<void> {
    await this.withMainWindowTarget(async (page) => {
      await page.clickText('返回工作台')
      await page.waitFor(`document.querySelector('section[aria-label="本地应用视图"]') === null`, {
        timeoutMs: 30_000,
        message: '本地应用视图未随「返回工作台」关闭',
      })
    })
  }

  // 回到工作台后右键项目卡片，通过菜单「停止应用」结束运行中的本地应用并确认角标消失。
  async stopLocalAppFromCard(title: string): Promise<void> {
    await this.withWorkbenchTarget(async (page) => {
      await this.openLocalProjects(page)
      await page.projectAction(title, 'context-menu')
      await page.clickText('停止应用')
      await page.waitFor(
        `(${projectSurfaceExpression(title)})?.querySelector('.dshDesktopProjectBadge[data-kind="running"]') === null`,
        { timeoutMs: 30_000, message: `本地应用未停止：${title}` },
      )
    })
  }

  async renameProject(from: string, to: string): Promise<void> {
    await this.withWorkbenchTarget(async (page) => {
      await this.openLocalProjects(page)
      await page.projectAction(from, 'rename')
      await page.waitFor(`document.querySelector('input[aria-label="项目名称"]') !== null`, {
        timeoutMs: 10_000,
        message: `项目未进入改名状态：${from}`,
      })
      await page.setValue('input[aria-label="项目名称"]', to)
      await page.key('input[aria-label="项目名称"]', 'Enter')
      await page.waitFor(`${projectSurfaceExpression(to)} !== null`, { timeoutMs: 10_000, message: `项目名称未更新：${to}` })
    })
  }

  async setProjectCover(title: string, cover: ProjectCoverToken): Promise<void> {
    await this.withWorkbenchTarget(async (page) => {
      await this.openLocalProjects(page)
      await page.projectAction(title, 'context-menu')
      await page.clickText('修改封面')
      await page.click(`button[data-cover="${cover}"]`)
      await page.waitFor(`(${projectArticleExpression(title)})?.getAttribute('data-cover') === ${JSON.stringify(cover)}`, {
        timeoutMs: 10_000,
        message: `项目封面未更新：${title}`,
      })
    })
  }

  async pinProject(title: string): Promise<void> {
    await this.withWorkbenchTarget(async (page) => {
      await this.openLocalProjects(page)
      await page.projectAction(title, 'context-menu')
      await page.clickText('置顶')
      await page.waitFor(`(${projectArticleExpression(title)})?.hasAttribute('data-pinned') === true`, {
        timeoutMs: 10_000,
        message: `项目未置顶：${title}`,
      })
    })
  }

  async removeProject(title: string, scope: 'unregister' | 'recycle'): Promise<void> {
    await this.withWorkbenchTarget(async (page) => {
      await this.openLocalProjects(page)
      await page.projectAction(title, 'context-menu')
      await page.clickText('删除项目')
      await page.waitFor(`${deleteDialogExpression(title)} !== null`, { timeoutMs: 10_000, message: `删除确认未显示：${title}` })
      if (scope === 'recycle') {
        await page.click('input[aria-label="移到 Windows 回收站"]')
        await page.setValue(`input[placeholder="${escapeCssAttribute(title)}"]`, title)
        await page.clickText('移到回收站')
      } else {
        await page.clickText('确认移除')
      }
      await page.waitFor(`${projectArticleExpression(title)} === null`, { timeoutMs: 30_000, message: `项目未从列表移除：${title}` })
    })
  }

  async quit(): Promise<void> {
    const session = this.session
    if (session === undefined) return
    this.session = undefined
    if (this.ownsSession) {
      this.ownsSession = false
      await session.tauri.execute(({ core }) => core.invoke('orderly_quit')).catch(() => undefined)
      await cleanupWdioSession(session)
    }
  }

  async exportDiagnostics(): Promise<string> {
    const session = this.requireSession()
    const button = await session.$('button=导出诊断')
    await button.click()
    const value = await session.$('[data-diagnostics-path]').getAttribute('data-diagnostics-path')
    if (value === null) throw new Error('诊断导出未返回路径')
    return value
  }

  fixturePath(name: string): string {
    const root = process.env.DSH_E2E_ROOT
    if (root === undefined || !isAbsolute(root)) throw new Error('DSH_E2E_ROOT 必须是绝对路径')
    const target = resolve(root, name)
    const relation = relative(root, target)
    if (relation.startsWith('..') || isAbsolute(relation)) throw new Error('Fixture 路径越界')
    return target
  }

  private requireSession(): WebdriverIO.Browser {
    if (this.session === undefined) throw new Error('桌面 E2E 会话尚未启动')
    return this.session
  }

  private async withWorkbenchTarget<T>(run: (page: CdpPage) => Promise<T>): Promise<T> {
    const session = this.requireSession()
    const frame = await session.$('iframe[title="DeepSeek Harness 工作台"]')
    await frame.waitForDisplayed({ timeout: 30_000 })
    const frameUrl = await frame.getAttribute('src')
    if (frameUrl === null) throw new Error('工作台 iframe 缺少受管地址')
    const target = await this.findWorkbenchTarget(frameUrl)
    const page = await CdpPage.connect(target.webSocketDebuggerUrl)
    try {
      return await run(page)
    } finally {
      page.close()
    }
  }

  /** 主窗口（tauri 壳层，非工作台 iframe）上下文中执行；用于本地应用视图断言。 */
  private async withMainWindowTarget(run: (page: CdpPage) => Promise<void>): Promise<void> {
    const targets = await this.cdpTargets()
    const main = targets.find((target) => target.type === 'page' && !target.url.includes('127.0.0.1:'))
    if (main?.webSocketDebuggerUrl === undefined) throw new Error('找不到主窗口 CDP target')
    const page = await CdpPage.connect(main.webSocketDebuggerUrl)
    try {
      await run(page)
    } finally {
      page.close()
    }
  }

  /** 枚举 WebView2 暴露的 CDP targets；服务未就绪时返回空列表，由调用方决定是否重试。 */
  private async cdpTargets(): Promise<CdpTarget[]> {
    const response = await fetch('http://127.0.0.1:9229/json/list')
    if (!response.ok) return []
    return await response.json() as CdpTarget[]
  }

  private async findWorkbenchTarget(frameUrl: string): Promise<CdpTarget> {
    const deadline = Date.now() + 30_000
    while (Date.now() < deadline) {
      const match = (await this.cdpTargets())
        .find((target) => target.type === 'iframe' && target.url === frameUrl)
      if (match?.webSocketDebuggerUrl !== undefined) return match
      await new Promise((resolveWait) => setTimeout(resolveWait, 100))
    }
    throw new Error(`找不到工作台 CDP target：${frameUrl}`)
  }

  private async openLocalProjects(page: CdpPage): Promise<void> {
    const pageOpen = `document.querySelector('section[aria-label="本地项目"]') !== null`
    if (!await page.evaluate<boolean>(pageOpen)) {
      await page.waitFor(`document.querySelector('button[aria-label="本地项目"]') !== null`, {
        timeoutMs: 30_000,
        message: '本地项目入口未随桌面插件挂载',
      })
      await page.click('button[aria-label="本地项目"]')
      await page.waitFor(pageOpen, { timeoutMs: 30_000, message: '本地项目页面未打开' })
    }
  }
}

interface CdpTarget {
  type: string
  url: string
  webSocketDebuggerUrl: string
}

class CdpPage {
  private sequence = 0
  private readonly pending = new Map<number, { resolve(value: unknown): void; reject(cause: unknown): void }>()
  private readonly diagnostics: string[] = []

  private constructor(private readonly socket: WebSocket) {
    socket.addEventListener('message', (event) => {
      const message = JSON.parse(String(event.data)) as { id?: number; result?: unknown; error?: { message?: string } }
      if (message.id === undefined) {
        this.recordEvent(message as CdpEvent)
        return
      }
      const receiver = this.pending.get(message.id)
      if (receiver === undefined) return
      this.pending.delete(message.id)
      if (message.error !== undefined) receiver.reject(new Error(message.error.message ?? 'CDP command failed'))
      else receiver.resolve(message.result)
    })
  }

  static async connect(url: string): Promise<CdpPage> {
    const socket = new WebSocket(url)
    await new Promise<void>((resolveOpen, reject) => {
      socket.addEventListener('open', () => resolveOpen(), { once: true })
      socket.addEventListener('error', () => reject(new Error('无法连接工作台 CDP target')), { once: true })
    })
    const page = new CdpPage(socket)
    await page.command('Runtime.enable', {})
    await page.command('Network.enable', {})
    return page
  }

  close(): void {
    this.socket.close()
  }

  async evaluate<T>(expression: string): Promise<T> {
    const response = await this.command('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }) as {
      result?: { value?: T; description?: string }
      exceptionDetails?: { text?: string; exception?: { description?: string } }
    }
    if (response.exceptionDetails !== undefined) {
      throw new Error(response.exceptionDetails.exception?.description ?? response.exceptionDetails.text ?? '工作台脚本执行失败')
    }
    return response.result?.value as T
  }

  async waitFor(expression: string, options: { timeoutMs: number; message: string }): Promise<void> {
    const deadline = Date.now() + options.timeoutMs
    while (Date.now() < deadline) {
      if (await this.evaluate<boolean>(`Boolean(${expression})`)) return
      await new Promise((resolveWait) => setTimeout(resolveWait, 100))
    }
    const visibleText = await this.evaluate<string>(`document.body?.innerText?.slice(0, 4000) ?? ''`)
    throw new Error(`${options.message}\n工作台当前内容：\n${visibleText}\n工作台诊断：\n${this.diagnostics.join('\n')}`)
  }

  async click(selector: string): Promise<void> {
    await this.waitFor(`document.querySelector(${JSON.stringify(selector)}) !== null`, {
      timeoutMs: 10_000,
      message: `找不到工作台元素：${selector}`,
    })
    await this.evaluate(`(() => {
      const element = document.querySelector(${JSON.stringify(selector)});
      if (!(element instanceof HTMLElement)) throw new Error('Element is not clickable');
      element.click();
      return true;
    })()`)
  }

  async clickText(text: string): Promise<void> {
    const button = buttonTextExpression(text)
    await this.waitFor(`${button} !== null`, { timeoutMs: 10_000, message: `找不到按钮：${text}` })
    await this.evaluate(`(() => { const button = ${button}; button.click(); return true })()`)
  }

  async setValue(selector: string, value: string): Promise<void> {
    await this.waitFor(`document.querySelector(${JSON.stringify(selector)}) !== null`, {
      timeoutMs: 10_000,
      message: `找不到输入控件：${selector}`,
    })
    await this.evaluate(`(() => {
      const element = document.querySelector(${JSON.stringify(selector)});
      if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement)) {
        throw new Error('Element does not accept a value');
      }
      const prototype = element instanceof HTMLInputElement
        ? HTMLInputElement.prototype
        : element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLSelectElement.prototype;
      Object.getOwnPropertyDescriptor(prototype, 'value').set.call(element, ${JSON.stringify(value)});
      element.dispatchEvent(new Event('input', { bubbles: true }));
      element.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    })()`)
  }

  async key(selector: string, key: string): Promise<void> {
    await this.evaluate(`(() => {
      const element = document.querySelector(${JSON.stringify(selector)});
      if (!(element instanceof HTMLElement)) throw new Error('Keyboard target not found');
      element.dispatchEvent(new KeyboardEvent('keydown', { key: ${JSON.stringify(key)}, bubbles: true }));
      return true;
    })()`)
  }

  async projectAction(title: string, action: 'click' | 'double-click' | 'context-menu' | 'rename'): Promise<void> {
    const surface = projectSurfaceExpression(title)
    await this.waitFor(`${surface} !== null`, { timeoutMs: 30_000, message: `找不到本地项目：${title}` })
    await this.evaluate(`(() => {
      const element = ${surface};
      if (!(element instanceof HTMLElement)) throw new Error('Project surface not found');
      if (${JSON.stringify(action)} === 'click') element.click();
      else if (${JSON.stringify(action)} === 'double-click') element.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, detail: 2 }));
      else if (${JSON.stringify(action)} === 'context-menu') element.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, clientX: 120, clientY: 120 }));
      else {
        element.click();
        element.dispatchEvent(new KeyboardEvent('keydown', { key: 'F2', bubbles: true }));
      }
      return true;
    })()`)
  }

  private command(method: string, params: Record<string, unknown>): Promise<unknown> {
    const id = ++this.sequence
    return new Promise((resolveCommand, reject) => {
      const timeoutId = setTimeout(() => {
        this.pending.delete(id)
        reject(new Error(`CDP command timed out: ${method}`))
      }, 30_000)
      this.pending.set(id, {
        resolve: (value) => { clearTimeout(timeoutId); resolveCommand(value) },
        reject: (cause) => { clearTimeout(timeoutId); reject(cause) },
      })
      this.socket.send(JSON.stringify({ id, method, params }))
    })
  }

  private recordEvent(event: CdpEvent): void {
    if (event.method === 'Network.requestWillBeSent') {
      const url = event.params?.request?.url
      if (typeof url === 'string' && url.includes('/api/')) this.pushDiagnostic(`REQUEST ${url}`)
      return
    }
    if (event.method === 'Network.responseReceived') {
      const response = event.params?.response
      if (typeof response?.url === 'string' && response.url.includes('/api/')) {
        this.pushDiagnostic(`RESPONSE ${String(response.status)} ${response.url}`)
      }
      return
    }
    if (event.method === 'Runtime.exceptionThrown') {
      const description = event.params?.exceptionDetails?.exception?.description ?? event.params?.exceptionDetails?.text
      if (typeof description === 'string') this.pushDiagnostic(`EXCEPTION ${description}`)
      return
    }
    if (event.method === 'Runtime.consoleAPICalled' && event.params?.type === 'error') {
      const values = event.params.args?.map((arg) => arg.value ?? arg.description).filter((value) => value !== undefined)
      if (values !== undefined) this.pushDiagnostic(`CONSOLE ${values.join(' ')}`)
    }
  }

  private pushDiagnostic(message: string): void {
    this.diagnostics.push(message)
    if (this.diagnostics.length > 80) this.diagnostics.shift()
  }
}

interface CdpEvent {
  method?: string
  params?: {
    request?: { url?: string }
    response?: { url?: string; status?: number }
    exceptionDetails?: { text?: string; exception?: { description?: string } }
    type?: string
    args?: Array<{ value?: unknown; description?: string }>
  }
}

function buttonTextExpression(text: string): string {
  return `Array.from(document.querySelectorAll('button')).find((button) => button.textContent?.trim() === ${JSON.stringify(text)}) ?? null`
}

function conversationContainsExpression(text: string): string {
  return `document.querySelector('[data-slot="conversation.session"]')?.textContent?.includes(${JSON.stringify(text)}) === true`
}

function projectArticleExpression(title: string): string {
  return `Array.from(document.querySelectorAll('article[data-project-id]')).find((article) => article.querySelector('h2')?.textContent?.trim() === ${JSON.stringify(title)}) ?? null`
}

function projectSurfaceExpression(title: string): string {
  return `(${projectArticleExpression(title)})?.querySelector('.dshDesktopProjectCardSurface') ?? null`
}

function deleteDialogExpression(title: string): string {
  return `Array.from(document.querySelectorAll('section[role="dialog"]')).find((dialog) => dialog.getAttribute('aria-label') === ${JSON.stringify(`删除 ${title}`)}) ?? null`
}

function escapeCssAttribute(value: string): string {
  return value.replaceAll('\\', '\\\\').replaceAll('"', '\\"')
}

function e2eEnvironment(): Record<string, string> {
  return {
    ...Object.fromEntries(Object.entries(process.env).filter(
    ([name, value]) => (
      name.startsWith('DSH_E2E_')
      || name.startsWith('DSH_DESKTOP_E2E_')
      || name === 'NODE_EXTRA_CA_CERTS'
      || name === 'DEEPSEEK_BASE_URL'
      || name === 'DEEPSEEK_API_KEY'
    ) && value !== undefined,
    )) as Record<string, string>,
    WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS: '--remote-debugging-port=9229',
  }
}
