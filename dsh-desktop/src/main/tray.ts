import { app, Menu, nativeImage, Tray } from 'electron'
import { existsSync } from 'node:fs'
import { join } from 'node:path'

export function trayIconPath(): string {
  const baseDir = app.isPackaged ? process.resourcesPath : process.cwd()
  return join(baseDir, 'resources', 'icon.ico')
}

export function createTray(onOpenHome: () => void, onCheckUpdate: () => void, onQuit: () => void): Tray {
  const img = nativeImage.createFromPath(trayIconPath())
  const tray = new Tray(img.isEmpty() ? nativeImage.createEmpty() : img.resize({ height: 16 }))
  if (img.isEmpty()) {
    void app.getFileIcon(process.execPath, { size: 'small' }).then((fallback) => tray.setImage(fallback))
  }
  tray.setToolTip('DeepSeek Harness')
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '打开主页', click: onOpenHome },
    { label: '检查更新', click: onCheckUpdate },
    { type: 'separator' },
    { label: '退出', click: onQuit },
  ]))
  return tray
}

export function windowIcon(): string | undefined {
  return existsSync(trayIconPath()) ? trayIconPath() : undefined
}
