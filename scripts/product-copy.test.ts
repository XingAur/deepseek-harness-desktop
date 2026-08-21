import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const productCopyFiles = [
  'index.html',
  'README.md',
  'runtime/README.md',
  '.github/workflows/desktop.yml',
  'src/App.tsx',
  'src-tauri/Cargo.toml',
  'src-tauri/tauri.conf.json',
  'src-tauri/tauri.windows.conf.json',
  'src-tauri/src/lib.rs',
  'src-tauri/src/window.rs',
  'src-tauri/src/runtime/health.rs',
  'src-tauri/src/generation/coordinator.rs',
  'src-tauri/src/desktop/coordinator.rs',
  'src-tauri/src/runtime/process.rs',
]

const removedCommunityModules = [
  'src/catalog.ts',
  'src/market-routes.ts',
  'src/plugin-command.ts',
  'src/client/MarketPage.tsx',
  'src/client/PluginDialog.tsx',
]

const currentProductSources = [
  'scripts/build-runtime.mjs',
  'src-tauri/src/runtime/process.rs',
  '.github/workflows/desktop.yml',
  'runtime/README.md',
]

const localProjectSources = [
  'packages/dsh-plugin-desktop/src/client/AdvancedFrame.tsx',
  'packages/dsh-plugin-desktop/src/client/LocalProjectsPage.tsx',
  'packages/dsh-plugin-desktop/src/client/ProjectContextMenu.tsx',
  'packages/dsh-plugin-desktop/src/client/ProjectDeleteDialog.tsx',
  'packages/dsh-plugin-desktop/src/client/project-controller.ts',
  'packages/dsh-plugin-desktop/src/client/advanced-shell.ts',
]

const abbreviatedProductCopy = [
  'DSH Desktop',
  'DSH Runtime',
  'DSH 工作台',
  '非 DSH 服务',
  '非受管 DSH 地址',
  'DSH 插件体系',
  'Find DSH Plugin',
]

describe('product copy', () => {
  it('keeps bootstrap scrolling native while styling a subtle themed scrollbar', () => {
    const css = readFileSync('src/app.css', 'utf8')

    expect(css).toContain('scrollbar-width: thin')
    expect(css).toContain('scrollbar-color:')
    expect(css).toContain('.bootstrapShell::-webkit-scrollbar')
    expect(css).toContain('.bootstrapShell::-webkit-scrollbar-thumb:hover')
    expect(css).not.toMatch(/\.bootstrapShell::?-webkit-scrollbar[^}]*display:\s*none/s)
  })

  it('presents the repository to ordinary users without advertising an unavailable download', () => {
    const readme = readFileSync('README.md', 'utf8')

    expect(readme).toContain('首个公开预览版正在准备中')
    expect(readme).toContain('首次打开应用')
    expect(readme).toContain('后续启动')
    expect(readme).toContain('本地项目')
    expect(readme).toContain('Profile')
    expect(readme).toContain('## 开发者指南')
    expect(readme).not.toMatch(/releases\/latest\/download\/.+setup/i)
    expect(readme).not.toContain('手机远程控制')
    expect(readme).not.toContain('社区插件市场')
  })

  it('ships one in-app runtime preparation surface', () => {
    const main = readFileSync('src/main.tsx', 'utf8')
    const lib = readFileSync('src-tauri/src/lib.rs', 'utf8')
    const installer = readFileSync('src-tauri/windows/installer.nsi', 'utf8')
    expect(main).not.toContain('ProvisioningApp')
    expect(main).not.toContain('mode=provisioning')
    expect(lib).not.toContain('provisioning::app::run')
    expect(installer).not.toContain('--provision-runtime')
    expect(readFileSync('src/App.tsx', 'utf8')).toContain('准备你的 DeepSeek Harness')
    expect(readFileSync('README.md', 'utf8')).toContain('首次打开应用')
  })

  it('does not expose installer-time Runtime provisioning', () => {
    const appMode = readFileSync('src-tauri/src/app_mode.rs', 'utf8')
    const productionAppMode = appMode.split('#[cfg(test)]')[0]
    const lib = readFileSync('src-tauri/src/lib.rs', 'utf8')
    expect(productionAppMode).not.toContain('InstallBundledRuntime')
    expect(productionAppMode).not.toContain('--install-bundled-runtime')
    expect(lib).not.toContain('installer_runtime')
    expect(lib).not.toContain('exit_after_bundled_runtime_install')
  })

  it('builds a profile-aware readiness proxy into the managed runtime', () => {
    const source = readFileSync('scripts/build-runtime.mjs', 'utf8')
    expect(source).toContain("healthPath: '/__desktop/health'")
    expect(source).toContain('DSH_DESKTOP_PROFILE_REVISION')
    expect(source).toContain("request.url === '/__desktop/control/health'")
  })

  it('hides Windows Runtime startup and shutdown helper windows', () => {
    const launcher = readFileSync('scripts/build-runtime.mjs', 'utf8')
    const runtimeProcess = readFileSync('src-tauri/src/runtime/process.rs', 'utf8')
    const windowsShutdownStart = runtimeProcess.indexOf('async fn terminate_tree(pid: u32)')
    const windowsShutdown = runtimeProcess.slice(
      windowsShutdownStart,
      runtimeProcess.indexOf('#[cfg(unix)]', windowsShutdownStart),
    )

    expect(launcher.match(/windowsHide: process\.platform === 'win32'/g)).toHaveLength(2)
    expect(runtimeProcess).toContain('const CREATE_NO_WINDOW: u32 = 0x0800_0000;')
    expect(windowsShutdown).toContain('.creation_flags(CREATE_NO_WINDOW)')
  })

  it('pins the managed Runtime to one DSH release candidate', () => {
    const source = readFileSync('scripts/build-runtime.mjs', 'utf8')
    const runtimeReadme = readFileSync('runtime/README.md', 'utf8')

    expect(source).toContain("const DSH_VERSION = '0.1.0-rc.8'")
    expect(source).not.toContain("const DSH_VERSION = '0.1.0-rc.7'")
    expect(runtimeReadme).toContain('DeepSeek Harness `0.1.0-rc.8`')
  })

  it('installs the required Runtime peers without npm peer backtracking', () => {
    const source = readFileSync('scripts/build-runtime.mjs', 'utf8')
    const peerSource = readFileSync('scripts/runtime-peer-dependencies.mjs', 'utf8')

    expect(peerSource).toContain("'@deepseek-ai/cordis-plugin-group': '1.0.1'")
    expect(peerSource).toContain("'@deepseek-ai/dsh-invariants': dshVersion")
    expect(peerSource).toContain("react: '18.3.1'")
    expect(peerSource).toContain("'react-dom': '18.3.1'")
    expect(source).toContain('runtimePeerDependencies(DSH_VERSION)')
    expect(source).toContain('assertRuntimePeerDependencies(appDir)')
    expect(source).toContain("'--legacy-peer-deps'")
    expect(source).not.toContain("'--no-legacy-peer-deps'")
  })

  it('does not ship community market modules', () => {
    for (const file of removedCommunityModules) {
      expect(existsSync(join('packages/dsh-plugin-desktop', file)), file).toBe(false)
    }
  })

  it('ships the simplified local project management surface', () => {
    const sources = localProjectSources.map((file) => readFileSync(file, 'utf8')).join('\n')
    const pluginPackage = JSON.parse(readFileSync('packages/dsh-plugin-desktop/package.json', 'utf8')) as { version: string }

    expect(sources).toContain('本地项目')
    expect(sources).toContain('workspaces.create')
    expect(sources).toContain('project.metadata.patch')
    expect(sources).toContain('project.directory.recycle')
    expect(sources).toContain('修改封面')
    expect(sources).toContain('移到 Windows 回收站')
    expect(sources).not.toContain('dshDesktopProjectCreatePanel')
    expect(sources).not.toMatch(/MarketPage|community\/plugins|PluginDialog|社区插件/)
    expect(pluginPackage.version).toBe('0.3.2')
  })

  it('does not reference the removed community catalog', () => {
    for (const file of currentProductSources) {
      expect(readFileSync(file, 'utf8'), file).not.toMatch(/community\.json|sign-catalog|DSH_DESKTOP_CATALOG/)
    }
  })

  it('does not allow a runtime manifest endpoint override in production', () => {
    const source = readFileSync('src-tauri/src/runtime/updater.rs', 'utf8')
    const policy = readFileSync('src-tauri/src/provisioning/source.rs', 'utf8')
    expect(source).not.toContain('std::env::var("DSH_DESKTOP_RUNTIME_MANIFEST_URL")')
    expect(policy).toContain(
      'option_env!("DSH_DESKTOP_RUNTIME_MANIFEST_URL")',
    )
    expect(policy).toContain('#[cfg(feature = "e2e")]')
    expect(policy).toContain('std::env::var("DSH_DESKTOP_E2E_RUNTIME_MANIFEST_URL")')
    expect(policy).toContain('"127.0.0.1" | "localhost"')
    expect(source).toContain('builder.danger_accept_invalid_certs(true)')
  })

  it('builds tagged releases with signed application updater artifacts', () => {
    const workflow = readFileSync('.github/workflows/desktop.yml', 'utf8')
    expect(workflow).toContain('TAURI_SIGNING_PRIVATE_KEY:')
    expect(workflow).toContain('TAURI_SIGNING_PRIVATE_KEY_PASSWORD:')
    expect(workflow).toContain('TAURI_UPDATER_PUBLIC_KEY:')
    expect(workflow).toContain('node scripts/write-updater-config.mjs')
    expect(workflow).toContain('signer generate --ci --write-keys')
    expect(workflow).toContain('--config src-tauri/tauri.release.conf.json')
    expect(workflow).toContain('uploadUpdaterJson: true')
    expect(workflow).toContain('updaterJsonPreferNsis: true')
    expect(workflow).toContain('uploadUpdaterSignatures: true')
  })

  it('requires the current managed Runtime identity for Windows releases', () => {
    const source = readFileSync('scripts/windows-installer.mjs', 'utf8')

    expect(source).toContain("manifest.version !== '0.1.2-preview'")
  })

  it('never replaces an existing managed Runtime release asset', () => {
    const workflow = readFileSync('.github/workflows/desktop.yml', 'utf8')
    const marker = '- name: Add managed Runtime files to draft release'
    const runtimeUpload = workflow.slice(workflow.indexOf(marker))

    expect(runtimeUpload).toContain('gh release upload')
    expect(runtimeUpload).not.toContain('--clobber')
  })

  it('always ships a parseable fallback updater configuration', () => {
    const config = JSON.parse(readFileSync('src-tauri/tauri.conf.json', 'utf8')) as {
      plugins?: { updater?: { pubkey?: string, endpoints?: string[] } }
    }
    expect(config.plugins?.updater).toBeTypeOf('object')
    expect(config.plugins?.updater?.pubkey).toMatch(/\S+/)
    expect(config.plugins?.updater?.endpoints).toEqual([
      'https://github.com/XingAur/DSH-Desktop/releases/latest/download/latest.json',
    ])
  })

  it('compiles webdriver only for e2e candidates', () => {
    const cargo = readFileSync('src-tauri/Cargo.toml', 'utf8')
    const lib = readFileSync('src-tauri/src/lib.rs', 'utf8')
    const e2eConfig = readFileSync('src-tauri/tauri.e2e.conf.json', 'utf8')
    const main = readFileSync('src/main.tsx', 'utf8')
    const packageJson = readFileSync('package.json', 'utf8')

    expect(cargo).toContain('dep:tauri-plugin-wdio')
    expect(cargo).toContain('dep:tauri-plugin-wdio-webdriver')
    expect(lib).toContain('#[cfg(feature = "e2e")]')
    expect(lib).toContain('tauri_plugin_wdio::init()')
    expect(e2eConfig).toContain('npm run build:web:e2e')
    expect(main).toContain("import.meta.env.MODE === 'e2e'")
    expect(main).toContain("import('@wdio/tauri-plugin')")
    expect(packageJson).toContain('"build:web:e2e"')
    expect(e2eConfig).toContain('"dangerousAcceptInvalidCerts": true')
    expect(readFileSync('src-tauri/tauri.conf.json', 'utf8')).not.toContain('dangerousAcceptInvalidCerts')
  })

  it('scopes installer cleanup to recorded absolute paths and pids', () => {
    const install = readFileSync('scripts/e2e/install-web-setup.ps1', 'utf8')
    const cleanup = readFileSync('scripts/e2e/verify-cleanup.ps1', 'utf8')
    const uninstall = readFileSync('scripts/e2e/uninstall-web-setup.ps1', 'utf8')

    expect(install).toContain('Resolve-Path -LiteralPath $InstallerPath')
    expect(cleanup).toContain('Get-Process -Id $record.desktopPid')
    expect(cleanup).toContain('$record.installRoot')
    expect(cleanup).not.toMatch(/Stop-Process\s+-Name/)
    expect(uninstall).toContain('$record.uninstallString')
    expect(uninstall).not.toMatch(/Remove-Item[^\r\n]+dataRoot/)
  })

  it('uses the full DeepSeek Harness name in user-facing sources', () => {
    const violations = productCopyFiles.flatMap((file) => {
      const source = readFileSync(file, 'utf8')
      return abbreviatedProductCopy
        .filter((copy) => source.includes(copy))
        .map((copy) => `${file}: ${copy}`)
    })

    expect(violations).toEqual([])
  })
})
