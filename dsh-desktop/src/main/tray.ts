import { app, Menu, nativeImage, Tray } from 'electron'

export function createTray(onOpenHome: () => void, onCheckUpdate: () => void, onQuit: () => void): Tray {
  const tray = new Tray(nativeImage.createEmpty())
  void app.getFileIcon(process.execPath, { size: 'small' }).then((img) => tray.setImage(img))
  tray.setToolTip('DeepSeek Harness')
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: '打开主页', click: onOpenHome },
    { label: '检查更新', click: onCheckUpdate },
    { type: 'separator' },
    { label: '退出', click: onQuit },
  ]))
  return tray
}
