import { browser } from '@wdio/globals'
import '@wdio/tauri-service'
import { PackagedDesktopHarness } from '../support/desktop'

// 启用条件（满足后删除 it.skip 即可运行冒烟）：
// 1. 完整 e2e 环境：npm run e2e:build 产出启用 e2e feature 的候选应用，并通过 npm run e2e 启动；
// 2. 受管项目根内预先存在清单项目「记账-e2e」——目录含 dsh-app.json 与 dist/index.html。
//    清单由真实模型按构建提示词落盘；确定性 E2E_PONG 假模型只回文本，不会写清单，
//    因此本用例不能依赖既有 e2e 流程自动产出该前置项目。
describe('local app launcher', () => {
  it.skip('launches a manifest project in the main window, returns and stops', async () => {
    const desktop = new PackagedDesktopHarness(browser)
    await desktop.launch()

    // 冒烟链路：双击可运行卡片 → 主窗口出现应用视图 → 返回工作台 → 右键卡片停止应用。
    await desktop.launchLocalApp('记账-e2e')
    await desktop.returnToWorkbenchFromApp()
    await desktop.stopLocalAppFromCard('记账-e2e')
  })
})
