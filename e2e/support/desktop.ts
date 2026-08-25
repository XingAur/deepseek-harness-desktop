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
  createConversation(prompt: string): Promise<void>
  continueConversation(prompt: string): Promise<void>
  assertSessionRoundTrip(markers: readonly string[]): Promise<void>
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
  private cdpEndpoint?: string

  constructor(session?: WebdriverIO.Browser) {
    this.session = session
    if (session !== undefined) this.cdpEndpoint = configuredCdpEndpoint()
  }

  async launch(appBinary = process.env.DSH_E2E_APP_BINARY): Promise<void> {
    if (appBinary === undefined || !isAbsolute(appBinary) || !existsSync(appBinary)) {
      throw new Error('E2E 应用路径无效')
    }
    if (this.session === undefined) {
      // E2E 构建在 WebView2 创建层固定配置 9229；不要依赖环境变量覆盖，
      // 因为 WebView2 不保证从 Tauri/WebDriver 的子进程环境读取该配置。
      this.cdpEndpoint = defaultCdpEndpoint
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
      }
      try {
        this.session = await startWdioSession(capabilities)
        this.ownsSession = true
      } catch (error) {
        this.cdpEndpoint = undefined
        throw error
      }
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
    const session = this.requireSession()
    await session.waitUntil(async () => {
      const value = await session.execute(() => document.body.dataset.runtimePid)
      const pid = Number(value)
      return Number.isSafeInteger(pid) && pid > 0
    }, { timeout: 30_000, timeoutMsg: '页面未暴露有效的测试 Runtime PID；活动 Runtime identity bridge 未就绪' })
    const value = await session.execute(() => document.body.dataset.runtimePid)
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
      await this.openLocalProjects(page)
      if (await this.dismissFirstUseNotice(page)) {
        await this.openLocalProjects(page)
      }

      await page.setValue('textarea[aria-label="项目需求"]', input.idea)
      await page.clickText('检查并预览')
      await page.clickText('确认并开始构建')
      const replyVisible = conversationContainsExpression('E2E_PONG')
      await page.waitFor(replyVisible, {
        timeoutMs: 60_000,
        message: '本地项目会话没有收到确定性模型回复',
      })
    })
  }

  async createConversation(prompt: string): Promise<void> {
    if (prompt.trim() === '') throw new Error('新会话消息不能为空')
    await this.withWorkbenchTarget(async (page) => {
      // 首启声明在工作台 shell 就绪后异步挂载。不能只做一次即时检查，
      // 否则它会在检查与点击「新建会话」之间出现，导致首个会话用例偶发失败。
      const newSessionButton = 'document.querySelector(\'button[aria-label="新建会话"]\')'
      const continueButton = firstUseContinueButtonExpression()
      await page.waitFor(`(${newSessionButton}) !== null || (${continueButton}) !== null`, {
        timeoutMs: 15_000,
        message: '工作台未进入可创建会话或确认首启声明的状态',
      })
      await this.dismissFirstUseNotice(page)
      await page.waitFor(`(${newSessionButton}) !== null`, {
        timeoutMs: 15_000,
        message: '关闭首次使用声明后未出现新建会话入口',
      })
      await page.click('button[aria-label="新建会话"]')
      const composer = conversationComposerExpression()
      await page.waitFor(`${composer} !== null`, {
        timeoutMs: 30_000,
        message: '新会话输入框未出现',
      })
      await page.setValueFromExpression(composer, prompt)
      await page.waitFor(`document.querySelector('button[aria-label="发送消息"]')?.disabled === false`, {
        timeoutMs: 10_000,
        message: '新会话发送按钮未启用',
      })
      await page.click('button[aria-label="发送消息"]')
      await page.waitFor(conversationContainsExpression(prompt), {
        timeoutMs: 30_000,
        message: `用户消息未实时显示：${prompt}`,
      })
      await page.waitFor(conversationContainsExpression('E2E_PONG'), {
        timeoutMs: 60_000,
        message: '新会话没有收到确定性模型回复',
      })
    })
  }

  async continueConversation(prompt: string): Promise<void> {
    if (prompt.trim() === '') throw new Error('继续会话消息不能为空')
    await this.withWorkbenchTarget(async (page) => {
      const composer = conversationComposerExpression()
      await page.waitFor(`${composer} !== null`, {
        timeoutMs: 30_000,
        message: '继续会话输入框未出现',
      })
      const previousPongCount = await page.evaluate<number>(conversationAssistantReplyCountExpression('E2E_PONG'))
      await page.setValueFromExpression(composer, prompt)
      await page.waitFor(conversationSendEnabledExpression(), {
        timeoutMs: 10_000,
        message: '继续会话发送按钮未启用',
      })
      await page.click('button[aria-label="发送消息"]')
      await page.waitFor(conversationContainsExpression(prompt), {
        timeoutMs: 30_000,
        message: `继续会话用户消息未实时显示：${prompt}`,
      })
      await page.waitFor(conversationAssistantReplyIncreaseExpression('E2E_PONG', previousPongCount), {
        timeoutMs: 60_000,
        message: '继续会话没有收到确定性模型回复',
      })
    })
  }

  async assertSessionRoundTrip(markers: readonly string[]): Promise<void> {
    if (markers.length === 0 || markers.some((marker) => marker.trim() === '')) {
      throw new Error('会话正文标记不能为空')
    }
    await this.withWorkbenchTarget(async (page) => {
      await this.ensureSidebarExpanded(page)
      const rows = sessionRowsExpression()
      await page.waitFor(`(${rows}).length >= 2`, {
        timeoutMs: 30_000,
        message: '会话列表少于 2 项',
      })
      const sequence = markers.length > 1 ? [...markers, markers[0]] : [...markers]
      const markerRows = new Map<string, number>()
      for (const marker of sequence) {
        const row = await this.openSessionContaining(page, marker)
        if (row === undefined) {
          throw new Error(`找不到包含正文标记的会话：${marker}`)
        }
        markerRows.set(marker, row)
      }
      assertSessionRoundTripCoverage(markers, markerRows)
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
    if (session === undefined) {
      this.cdpEndpoint = undefined
      return
    }
    this.session = undefined
    try {
      if (this.ownsSession) {
        this.ownsSession = false
        await session.tauri.execute(({ core }) => core.invoke('orderly_quit')).catch(() => undefined)
        await cleanupWdioSession(session)
      }
    } finally {
      this.cdpEndpoint = undefined
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
    const { targets } = await this.cdpTargets()
    const main = targets.find((target) => target.type === 'page' && !target.url.includes('127.0.0.1:'))
    if (main?.webSocketDebuggerUrl === undefined) throw new Error('找不到主窗口 CDP target')
    const page = await CdpPage.connect(main.webSocketDebuggerUrl)
    try {
      await run(page)
    } finally {
      page.close()
    }
  }

  /** 枚举当前 launch 的 WebView2 CDP targets；服务未就绪时把连接拒绝交由调用方重试。 */
  private async cdpTargets(): Promise<CdpTargetLookup> {
    const endpoint = this.cdpEndpoint
    if (endpoint === undefined) throw new Error('当前 E2E 会话未配置 CDP endpoint')
    let response: Response
    try {
      response = await fetch(new URL('/json/list', endpoint))
    } catch (error) {
      // 新进程启动时，WebView2 的 CDP 端口可能尚未开始监听；交由 findWorkbenchTarget 轮询。
      if (isCdpConnectionRefused(error)) return { state: 'connection-refused', targets: [] }
      throw error
    }
    if (!response.ok) throw new Error(`CDP target endpoint 返回 ${response.status}`)
    return { state: 'ready', targets: await response.json() as CdpTarget[] }
  }

  private requireCdpEndpoint(): string {
    if (this.cdpEndpoint === undefined) throw new Error('当前 E2E 会话未配置 CDP endpoint')
    return this.cdpEndpoint
  }

  private async findWorkbenchTarget(frameUrl: string): Promise<CdpTarget & { webSocketDebuggerUrl: string }> {
    const endpoint = this.requireCdpEndpoint()
    const deadline = Date.now() + 30_000
    let lastLookup: CdpTargetLookup = { state: 'connection-refused', targets: [] }
    while (Date.now() < deadline) {
      const lookup = await this.cdpTargets()
      lastLookup = lookup
      const { targets } = lookup
      const match = selectWorkbenchCdpTarget(targets, frameUrl)
      if (match !== undefined) return match
      await new Promise((resolveWait) => setTimeout(resolveWait, 100))
    }
    throw new Error(
      `找不到工作台 CDP target：${summarizeCdpUrl(frameUrl)}；最后 CDP endpoint ${summarizeCdpUrl(endpoint)}：${summarizeCdpTargetLookup(lastLookup)}`,
    )
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

  private async dismissFirstUseNotice(page: CdpPage): Promise<boolean> {
    const continueButton = firstUseContinueButtonExpression()
    if (!await page.evaluate<boolean>(`(${continueButton}) !== null`)) return false
    await page.evaluate(`(() => {
      const button = ${continueButton};
      if (!(button instanceof HTMLElement)) throw new Error('首次使用声明确认按钮不可点击');
      button.click();
      return true;
    })()`)
    await page.waitFor(`(${continueButton}) === null`, {
      timeoutMs: 10_000,
      message: '首次使用声明未关闭',
    })
    return true
  }

  private async ensureSidebarExpanded(page: CdpPage): Promise<void> {
    const frame = '.dshDesktopFrame'
    const openButton = 'button[aria-label="打开侧边栏"]'
    await page.waitFor(`document.querySelector(${JSON.stringify(frame)}) !== null`, {
      timeoutMs: 30_000,
      message: '桌面插件根节点未挂载',
    })
    if (await page.evaluate<boolean>(`document.querySelector(${JSON.stringify(frame)})?.hasAttribute('data-sidebar-collapsed') === true`)) {
      await page.waitFor(`document.querySelector(${JSON.stringify(openButton)}) !== null`, {
        timeoutMs: 10_000,
        message: '找不到打开侧边栏按钮',
      })
      await page.click(openButton)
    }
    await page.waitFor(`document.querySelector(${JSON.stringify(frame)})?.hasAttribute('data-sidebar-collapsed') === false`, {
      timeoutMs: 10_000,
      message: '工作台侧边栏未展开',
    })
  }

  private async openSessionContaining(page: CdpPage, marker: string): Promise<number | undefined> {
    const rows = sessionRowsExpression()
    const initialCount = await page.evaluate<number>(`(${rows}).length`)
    for (let attempt = 0; attempt < Math.max(3, initialCount * 3); attempt += 1) {
      const rowIndex = attempt % Math.max(1, initialCount)
      const clicked = await page.evaluate<boolean>(`(() => {
        const candidates = ${rows};
        const row = candidates[${rowIndex} % Math.max(1, candidates.length)];
        if (!(row instanceof HTMLElement)) return false;
        row.click();
        return true;
      })()`)
      if (!clicked) {
        await page.waitForValue(`(${rows}).length > 0`, 500)
        continue
      }
      if (await page.waitForValue(conversationContainsExpression(marker), 3_000)) return rowIndex
    }
    return undefined
  }
}

export function assertSessionRoundTripCoverage(markers: readonly string[], markerRows: ReadonlyMap<string, number>): void {
  const rows = markers.map((marker) => {
    const row = markerRows.get(marker)
    if (!Number.isSafeInteger(row) || row < 0) throw new Error(`找不到包含正文标记的会话：${marker}`)
    return row
  })
  if (new Set(rows).size < 2) throw new Error('会话轮转未覆盖至少两个独立会话')
}

export interface CdpTarget {
  type: string
  url: string
  webSocketDebuggerUrl?: string
}

export type CdpTargetLookup = {
  state: 'connection-refused' | 'ready'
  targets: readonly CdpTarget[]
}

export function selectWorkbenchCdpTarget(
  targets: readonly CdpTarget[],
  frameUrl: string,
): (CdpTarget & { webSocketDebuggerUrl: string }) | undefined {
  return targets.find((target): target is CdpTarget & { webSocketDebuggerUrl: string } => (
    target.url === frameUrl && typeof target.webSocketDebuggerUrl === 'string' && target.webSocketDebuggerUrl !== ''
  ))
}

export function summarizeCdpTargets(targets: readonly CdpTarget[]): string {
  if (targets.length === 0) return '无'
  return targets.map((target) => `${target.type} ${summarizeCdpUrl(target.url)}`).join(', ')
}

export function summarizeCdpTargetLookup(lookup: CdpTargetLookup): string {
  if (lookup.state === 'connection-refused') {
    return '连接被拒绝（请检查 WebView2 是否收到 --remote-debugging-port 参数）'
  }
  if (lookup.targets.length === 0) return 'endpoint HTTP 200，空 target 列表'
  return `endpoint HTTP 200，targets：${summarizeCdpTargets(lookup.targets)}`
}

function summarizeCdpUrl(rawUrl: string): string {
  try {
    const url = new URL(rawUrl)
    const queryKeys = [...new Set(url.searchParams.keys())].sort()
    return `${url.origin}${url.pathname}${queryKeys.length === 0 ? '' : `?${queryKeys.join('&')}`}`
  } catch {
    return '<无效 URL>'
  }
}

function isCdpConnectionRefused(error: unknown): boolean {
  if (!(error instanceof TypeError)) return false
  const cause = (error as { cause?: unknown }).cause
  if (typeof cause !== 'object' || cause === null) return false
  return (cause as { code?: unknown }).code === 'ECONNREFUSED'
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
    if (await this.waitForValue(expression, options.timeoutMs)) return
    const visibleText = await this.evaluate<string>(`document.body?.innerText?.slice(0, 4000) ?? ''`)
    throw new Error(`${options.message}\n工作台当前内容：\n${visibleText}\n工作台诊断：\n${this.diagnostics.join('\n')}`)
  }

  async waitForValue(expression: string, timeoutMs: number): Promise<boolean> {
    const deadline = Date.now() + timeoutMs
    while (Date.now() < deadline) {
      if (await this.evaluate<boolean>(`Boolean(${expression})`)) return true
      await new Promise((resolveWait) => setTimeout(resolveWait, 100))
    }
    return false
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
    await this.setValueFromExpression(`document.querySelector(${JSON.stringify(selector)})`, value)
  }

  async setValueFromExpression(expression: string, value: string): Promise<void> {
    await this.evaluate(`(() => {
      const element = ${expression};
      if (!(element instanceof HTMLInputElement || element instanceof HTMLTextAreaElement || element instanceof HTMLSelectElement)) {
        throw new Error('Element does not accept a value');
      }
      const prototype = element instanceof HTMLInputElement
        ? HTMLInputElement.prototype
        : element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLSelectElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
      if (setter === undefined) throw new Error('Element value setter is unavailable');
      setter.call(element, ${JSON.stringify(value)});
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
      return
    }
    if (event.method === 'Network.webSocketFrameReceived' || event.method === 'Network.webSocketFrameSent') {
      const payload = event.params?.response?.payloadData
      if (typeof payload === 'string' && (payload.includes('session/') || payload.includes('host/session'))) {
        const direction = event.method === 'Network.webSocketFrameReceived' ? 'WS IN' : 'WS OUT'
        this.pushDiagnostic(`${direction} ${payload.slice(0, 600)}`)
      }
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
    response?: { url?: string; status?: number; payloadData?: string }
    exceptionDetails?: { text?: string; exception?: { description?: string } }
    type?: string
    args?: Array<{ value?: unknown; description?: string }>
  }
}

function buttonTextExpression(text: string): string {
  return `Array.from(document.querySelectorAll('button')).find((button) => button.textContent?.trim() === ${JSON.stringify(text)}) ?? null`
}

function visibleButtonTextExpression(text: string): string {
  return `Array.from(document.querySelectorAll('button')).find((button) => button.textContent?.trim() === ${JSON.stringify(text)} && button.getClientRects().length > 0 && !button.disabled) ?? null`
}

function firstUseContinueButtonExpression(): string {
  return `Array.from(document.querySelectorAll('button')).find((button) => ['继续', 'Continue'].includes(button.textContent?.trim() ?? '') && button.getClientRects().length > 0 && !button.disabled) ?? null`
}

function conversationContainsExpression(text: string): string {
  return `document.querySelector('[data-slot="conversation.session"]')?.textContent?.includes(${JSON.stringify(text)}) === true`
}

export function conversationAssistantReplyCountExpression(text: string): string {
  return `(() => {
    const session = document.querySelector('[data-slot="conversation.session"]')
    if (session === null) return 0
    return Array.from(session.querySelectorAll('[data-chat-flow-kind="assistant-step"]'))
      .filter((node) => node.textContent?.includes(${JSON.stringify(text)}) === true)
      .length
  })()`
}

export function conversationAssistantReplyIncreaseExpression(text: string, previousCount: number): string {
  if (!Number.isSafeInteger(previousCount) || previousCount < 0) throw new Error('助手回复计数无效')
  return `(${conversationAssistantReplyCountExpression(text)}) > ${previousCount}`
}

export function conversationSendEnabledExpression(): string {
  return `document.querySelector('button[aria-label="发送消息"]')?.disabled === false`
}

function conversationComposerExpression(): string {
  return `Array.from(document.querySelectorAll('textarea')).find((textarea) => textarea.placeholder === '给智能体发消息' || textarea.placeholder === '描述你想要构建的内容') ?? null`
}

function sessionRowsExpression(): string {
  return `Array.from(document.querySelectorAll('.dshDesktopUpstreamSidebar [role="treeitem"][aria-selected]'))`
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

const defaultCdpEndpoint = 'http://127.0.0.1:9229'

export function normalizeE2eCdpEndpoint(rawEndpoint: string): string {
  let endpoint: URL
  try {
    endpoint = new URL(rawEndpoint)
  } catch {
    throw new Error('DSH_E2E_CDP_ENDPOINT 无效')
  }
  if (
    endpoint.protocol !== 'http:'
    || !['127.0.0.1', '::1', '[::1]'].includes(endpoint.hostname)
    || endpoint.port === ''
    || endpoint.username !== ''
    || endpoint.password !== ''
    || endpoint.pathname !== '/'
    || endpoint.search !== ''
    || endpoint.hash !== ''
  ) {
    throw new Error('DSH_E2E_CDP_ENDPOINT 必须是无查询参数的 loopback HTTP 地址')
  }
  return endpoint.origin
}

function configuredCdpEndpoint(): string {
  // 仅用于调用方自己已启动的 WebDriver session；内部 launch 固定使用 E2E 构建的 9229。
  return normalizeE2eCdpEndpoint(process.env.DSH_E2E_CDP_ENDPOINT ?? defaultCdpEndpoint)
}
