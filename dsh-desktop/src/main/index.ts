import { app, BrowserWindow, dialog, ipcMain, WebContentsView } from 'electron'
import { spawn } from 'node:child_process'
import { join } from 'node:path'
import { appPaths, dirWritable, loadSettings, resolveDataRoot, saveSettings } from './paths.js'
import { createLogger } from './logger.js'
import { probe } from './port-probe.js'
import { resolveDsh } from './dsh-resolver.js'
import { buildChildEnv, ensurePnpmShim } from './pnpm-runtime.js'
import { listProjects } from './projects.js'
import { startStaticServer } from './static-server.js'
import { ServiceManager, type SessionState } from './service-manager.js'
import { fetchLatest, installUserVersion } from './updater.js'
import { createTray, windowIcon } from './tray.js'

const isDev = !app.isPackaged
const baseDir = isDev ? process.cwd() : process.resourcesPath
const bundledDshDir = join(baseDir, 'resources', 'dsh')
const bundledPnpmJs = join(baseDir, 'resources', 'runtime-pnpm', 'node_modules', 'pnpm', 'bin', 'pnpm.mjs')

let win: BrowserWindow | null = null
let contentView: WebContentsView | null = null
let state: SessionState = 'idle'

const dataRoot = resolveDataRoot(undefined, () => dirWritable('D:\\'))
const settings = (() => { const s = loadSettings(join(dataRoot, 'settings.json')); saveSettings(join(dataRoot, 'settings.json'), { ...s, dataRoot }); return s })()

const paths = appPaths(dataRoot)
const mainLog = createLogger(paths.logsDir, 'main', 7)
const dshLog = createLogger(paths.logsDir, 'dsh', 7)

const mgr = new ServiceManager({
  sessionPort: 3080,
  probe: (p) => probe(p),
  spawnChild: (cmd, args, cwd, env) => {
    mainLog(`spawn ${cmd} ${args.join(' ')} (cwd=${cwd})`)
    const child = args.length > 0
      ? spawn(cmd, args, { env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true })
      : spawn(cmd, { shell: true, cwd, env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true })
    child.stdout?.on('data', (d) => dshLog(String(d).trim()))
    child.stderr?.on('data', (d) => dshLog(`[err] ${String(d).trim()}`))
    return child
  },
  treeKill: (pid) => { spawn('taskkill', ['/pid', String(pid), '/T', '/F']) },
  staticServe: async (dir) => (await startStaticServer(dir)).port,
  log: (l) => mainLog(l),
  onState: (s) => { state = s },
  startTimeoutMs: 30000,
  pollIntervalMs: 500,
})

function childEnv() {
  ensurePnpmShim(paths.binDir, process.execPath, bundledPnpmJs)
  return buildChildEnv({ binDir: paths.binDir, dshHome: paths.dshHome })
}

function createWindow(): void {
  win = new BrowserWindow({ width: 1280, height: 840, show: false, icon: windowIcon() })
  const bar = new WebContentsView({})
  bar.webContents.loadFile(join(baseDir, 'dist', 'renderer', 'bar.html'))
  win.contentView.addChildView(bar)
  contentView = new WebContentsView({})
  win.contentView.addChildView(contentView)
  const layout = () => {
    const [w, h] = win!.getContentSize()
    bar.setBounds({ x: 0, y: 0, width: w, height: 32 })
    contentView!.setBounds({ x: 0, y: 32, width: w, height: h - 32 })
  }
  win.on('resize', layout)
  layout()
  win.once('ready-to-show', () => win?.show())
  showHome()
}

function loadInContent(url: string): void {
  contentView?.webContents.loadURL(url)
}

function showHome(): void {
  loadInContent(`file://${join(baseDir, 'dist', 'renderer', 'home.html').replace(/\\/g, '/')}`)
}

function showError(msg: string): void {
  const u = `file://${join(baseDir, 'dist', 'renderer', 'error.html').replace(/\\/g, '/')}?msg=${encodeURIComponent(msg)}&log=${encodeURIComponent(join(paths.logsDir, 'dsh-*.log'))}`
  loadInContent(u)
}

ipcMain.handle('projects:list', () => listProjects(paths.projectsDir).map(p => ({ name: p.name, desc: p.desc, icon: p.icon })))
ipcMain.handle('open', async (_e, target: string) => {
  try {
    if (target === 'home') { showHome(); return { ok: true } }
    if (target === 'session') {
      const dsh = resolveDsh(paths.runtimeDshDir, bundledDshDir)
      const r = await mgr.ensureSession(dsh.binPath, childEnv())
      loadInContent(r.url)
      return { ok: true }
    }
    if (target.startsWith('project:')) {
      const name = target.slice('project:'.length)
      const p = listProjects(paths.projectsDir).find(x => x.name === name)
      if (!p) return { ok: false, error: 'PROJECT_NOT_FOUND' }
      if (p.start) {
        const ok = dialog.showMessageBoxSync(win!, {
          type: 'question', buttons: ['取消', '启动'],
          message: `首次启动项目「${p.name}」`, detail: '该项目将在本机执行启动命令，仅启动你信任的项目。',
        }) === 1
        if (!ok) return { ok: false, error: 'CANCELLED' }
      }
      const url = await mgr.ensureProject(p, childEnv())
      loadInContent(url)
      return { ok: true }
    }
    return { ok: false, error: 'BAD_TARGET' }
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    if (msg === 'PORT_CONFLICT') showError('端口 3080 被其他程序占用，请关闭后重试')
    else if (msg === 'START_TIMEOUT') showError('服务启动超时（30s）')
    else showError(`启动失败：${msg}`)
    return { ok: false, error: msg }
  }
})
ipcMain.handle('retry', () => { showHome(); return { ok: true } })
ipcMain.handle('state', () => state)

async function checkUpdate(): Promise<void> {
  try {
    const latest = await fetchLatest()
    const cur = resolveDsh(paths.runtimeDshDir, bundledDshDir)
    if (latest.version === cur.version) { dialog.showMessageBoxSync(win!, { message: `已是最新版本 ${cur.version}` }); return }
    const yes = dialog.showMessageBoxSync(win!, {
      type: 'question', buttons: ['取消', '升级'],
      message: `发现新版本 ${latest.version}（当前 ${cur.version}）`,
    }) === 1
    if (!yes) return
    await installUserVersion(paths.runtimeDshDir, latest.version)
    saveSettings(join(dataRoot, 'settings.json'), { ...settings, dataRoot, dshVersion: latest.version })
    dialog.showMessageBoxSync(win!, { message: `已安装 ${latest.version}，返回主页重新进入生效` })
    showHome()
  } catch (e) {
    dialog.showErrorBox('检查更新失败', e instanceof Error ? e.message : String(e))
  }
}

if (!app.requestSingleInstanceLock()) app.quit()
else {
  app.on('second-instance', () => { win?.show(); win?.focus() })
  app.whenReady().then(() => {
    createTray(showHome, checkUpdate, async () => { await mgr.shutdownAll(); app.quit() })
    createWindow()
  })
  app.on('window-all-closed', () => { /* 托盘常驻 */ })
}
