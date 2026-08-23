import { createRoot } from 'react-dom/client'
import { AppUpdateBanner } from '../src/App'
import type { AppUpdateState } from '../src/runtime-contract'
import '../src/app.css'

const scenario = new URLSearchParams(window.location.search).get('scenario') ?? 'mac'
const states: Record<string, AppUpdateState> = {
  mac: {
    phase: 'available',
    update: {
      version: '0.1.13',
      notes: '同步新版 DeepSeek Harness，并保留现有 Profile、项目、Skills、MCP 配置和本地数据。',
      size: 86 * 1024 * 1024,
      mode: 'manual-dmg',
      downloadUrl: 'https://github.com/XingAur/deepseek-harness-desktop/releases/download/desktop-v0.1.13/DeepSeek.Harness.Desktop_0.1.13_aarch64.dmg',
      developerIdSigned: false,
      notarized: false,
    },
  },
  'windows-ready': {
    phase: 'ready',
    update: {
      version: '0.1.13',
      notes: '新版已完成签名校验，可以立即重启安装，也可以安排在退出时安装。',
      size: 92 * 1024 * 1024,
      mode: 'in-app',
    },
  },
  failed: {
    phase: 'failed',
    update: { code: 'check', message: '更新服务器暂时不可用', recoverable: true },
  },
}

const state = states[scenario] ?? states.mac
const container = document.getElementById('root')
if (container === null) throw new Error('Missing review root')

createRoot(container).render(
  <main className="windowShell" data-review-scenario={scenario}>
    <div className="windowContent">
      <AppUpdateBanner
        state={state}
        receipt={null}
        installOnExit={false}
        diagnosticPath={scenario === 'failed' ? '/Users/demo/Desktop/dsh-diagnostics.zip' : null}
        onDownload={() => undefined}
        onInstallNow={() => undefined}
        onInstallOnExit={() => undefined}
        onDefer={() => undefined}
        onOpenManual={() => undefined}
        onRetry={() => undefined}
        onDismissReceipt={() => undefined}
        onExportDiagnostics={() => undefined}
      />
      <section className="bootstrapShell" aria-label="更新界面审查背景">
        <div className="bootstrapCard">
          <p className="eyebrow">DEEPSEEK HARNESS DESKTOP</p>
          <h1>更新界面审查</h1>
          <p className="statusMessage">此页面只渲染生产更新组件，不连接网络或本地 Runtime。</p>
        </div>
      </section>
    </div>
  </main>,
)
