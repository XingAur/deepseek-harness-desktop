import { existsSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { loadReleaseVersions } from './release-versions.mjs'

const releaseVersions = loadReleaseVersions()

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

  it('keeps keyboard focus visible on every application-update action', () => {
    const css = readFileSync('src/app.css', 'utf8')

    expect(css).toContain('.updatePrimaryButton:focus-visible')
    expect(css).toContain('.updateSecondaryButton:focus-visible')
    expect(css).toContain('.updateTextButton:focus-visible')
    expect(css).toContain('outline-offset: 2px')
  })

  it('presents the released product and its platform-specific update paths to ordinary users', () => {
    const readme = readFileSync('README.md', 'utf8')

    expect(readme).toContain('https://github.com/XingAur/deepseek-harness-desktop/releases')
    expect(readme).toContain('Windows x64')
    expect(readme).toContain('应用内更新')
    expect(readme).toContain('不等同于付费的 Windows Authenticode 发行者证书')
    expect(readme).toContain('未知发布者')
    expect(readme).toContain('macOS Apple Silicon')
    expect(readme).toContain('手动替换')
    expect(readme).toContain('未使用 Apple Developer ID 签名、未经过 Apple 公证')
    expect(readme).toContain('每天 10:30')
    expect(readme).toContain('Profile、Workspace 和会话数据')
    expect(readme).toContain('npm run release:prepare -- --latest=<已确认存在的精确版本>')
    expect(readme).not.toContain('--dsh-version=')
    expect(readme).toContain('首次打开应用')
    expect(readme).toContain('后续启动')
    expect(readme).toContain('本地项目')
    expect(readme).toContain('文档\\DeepSeek Harness\\Projects')
    expect(readme).toContain('Profile')
    expect(readFileSync('packages/dsh-plugin-desktop/README.md', 'utf8')).toContain('项目路径、Profile 和权限由桌面端自动处理')
    expect(readme).toContain('## 开发者指南')
    expect(readme).not.toMatch(/releases\/latest\/download\/.+setup/i)
    expect(readme).not.toContain('首个公开版本还需要完成')
    expect(readme).not.toContain('尚未发布项目所需的不可变 Runtime Release')
    expect(readme).not.toContain('手机远程控制')
    expect(readme).not.toContain('社区插件市场')
  })

  it('documents the governed extension-platform foundation without claiming integrations are complete', () => {
    const path = 'docs/architecture/extension-platform.md'
    expect(existsSync(path)).toBe(true)
    const architecture = readFileSync(path, 'utf8')

    for (const required of [
      'Codex',
      'Claude',
      'API Provider',
      'CLI Worker',
      'Plugins',
      'Skills',
      'MCP',
      'Profile',
      '权限',
      '审计',
      '回滚',
      '凭证',
    ]) {
      expect(architecture, required).toContain(required)
    }
    expect(architecture).toContain('本期没有实现')
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

  it('keeps the Runtime manifest health path aligned with the readiness proxy', () => {
    const source = readFileSync('scripts/build-runtime.mjs', 'utf8')
    const launcher = readFileSync('scripts/runtime-launcher.mjs', 'utf8')
    const manifestSource = readFileSync('scripts/runtime-release-manifest.mjs', 'utf8')
    expect(manifestSource).toContain("healthPath: '/__desktop/health'")
    expect(source).toContain("import { writeRuntimeLauncher } from './runtime-launcher.mjs'")
    expect(launcher).toContain("request.url === '/__desktop/health'")
    expect(launcher).toContain('DSH_DESKTOP_PROFILE_REVISION')
    expect(launcher).toContain("request.url === '/__desktop/control/health'")
  })

  it('runs the Session contract gate after assembly and before creating release artifacts', () => {
    const source = readFileSync('scripts/build-runtime.mjs', 'utf8')
    expect(source).toContain("import { materializeRuntimeLinks } from './materialize-runtime-links.mjs'")
    expect(source).toContain('materializeRuntimeLinks(stage, output)')
    expect(source).toContain("'scripts/run-runtime-session-contract.mjs'")

    const inspect = source.indexOf('inspectAssembledRuntimeCapabilities(appDir')
    const launcher = source.indexOf('writeRuntimeLauncher(appDir')
    const links = source.indexOf('materializeRuntimeLinks(stage, output)')
    const contract = source.indexOf("'scripts/run-runtime-session-contract.mjs'")
    const archive = source.indexOf('const archive = join(output')
    const manifest = source.indexOf('writeUnsignedRuntimeManifest({')

    expect(inspect).toBeGreaterThan(-1)
    expect(inspect).toBeLessThan(launcher)
    expect(launcher).toBeLessThan(links)
    expect(links).toBeLessThan(contract)
    expect(contract).toBeLessThan(archive)
    expect(archive).toBeLessThan(manifest)
  })

  it('ships and attaches the managed Runtime WebSocket event proxy', () => {
    const source = readFileSync('scripts/build-runtime.mjs', 'utf8')
    const launcher = readFileSync('scripts/runtime-launcher.mjs', 'utf8')
    expect(source).toContain("cpSync(join('scripts', 'runtime-websocket-proxy.mjs'")
    expect(launcher).toContain("import { attachRuntimeWebSocketProxy } from './runtime-websocket-proxy.mjs'")
    expect(launcher).toContain('attachRuntimeWebSocketProxy(proxy, { port: backendPort })')
  })

  it('hides Windows Runtime startup and shutdown helper windows', () => {
    const launcher = readFileSync('scripts/runtime-launcher.mjs', 'utf8')
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

    expect(source).toContain("import { loadReleaseVersions } from './release-versions.mjs'")
    expect(source).toContain('const DSH_VERSION = versions.dshVersion')
    expect(source).not.toMatch(/const DSH_VERSION = ['"]/)
    expect(runtimeReadme).toContain('versions pinned in')
    expect(runtimeReadme).toContain('`release/versions.json`')
    expect(runtimeReadme).not.toContain(`DeepSeek Harness \`${releaseVersions.dshVersion}\``)
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

  it('builds signed Windows updater artifacts without direct Tauri release publication', () => {
    const workflow = readFileSync('.github/workflows/desktop.yml', 'utf8')
    expect(workflow).toContain('TAURI_SIGNING_PRIVATE_KEY:')
    expect(workflow).toContain('TAURI_SIGNING_PRIVATE_KEY_PASSWORD:')
    expect(workflow).toContain('TAURI_UPDATER_PUBLIC_KEY:')
    expect(workflow).toContain('node scripts/write-updater-config.mjs')
    expect(workflow).toContain('signer generate --ci --write-keys')
    expect(workflow).toContain('--config src-tauri/tauri.release.conf.json')
    expect(workflow).toContain('--platform=windows-x86_64')
    expect(workflow).toContain('--platform=darwin-aarch64')
    expect(workflow).toContain('node scripts/desktop-release.mjs')
    expect(workflow).not.toContain('tagName:')
    expect(workflow).not.toContain('releaseDraft:')
    expect(workflow).not.toContain('includeUpdaterJson:')
    expect(workflow).not.toContain('updaterJsonPreferNsis:')
    expect(workflow).toContain('secrets.DSH_DESKTOP_SIGNING_PRIVATE_KEY')
    expect(workflow).not.toContain('runtime-signing-state.mjs')
    expect(workflow).not.toContain('wbAbExHsjryIT22fTuRA3W61tJdaXFC7YxoAeN9uKnQ')
    expect(workflow).toContain('- name: Verify Runtime manifest')
    expect(workflow).toContain('node scripts/verify-runtime-manifest.mjs')
  })

  it('requires the current managed Runtime identity for Windows releases', () => {
    const source = readFileSync('scripts/windows-installer.mjs', 'utf8')
    const workflow = readFileSync('.github/workflows/desktop.yml', 'utf8')

    expect(source).toContain('export const MANAGED_RUNTIME_VERSION = loadReleaseVersions().runtimeVersion')
    expect(workflow).toContain('MANAGED_RUNTIME_VERSION:v.runtimeVersion')
    expect(workflow).toContain(
      'releases/download/runtime-v${MANAGED_RUNTIME_VERSION}/${ARCHIVE_NAME}',
    )
    expect(workflow).toContain(
      "tauri_args: '--config src-tauri/target/windows-installer/tauri.windows-installer.conf.json'",
    )
    expect(workflow).toContain('cp "${RUNTIME_DIR}/${ARCHIVE_NAME}" "runtime/${ARCHIVE_NAME}"')
    expect(readFileSync('scripts/write-updater-config.mjs', 'utf8')).toContain(
      "resources: { '../runtime/': 'runtime/' }",
    )
    const prepareConfig = workflow.indexOf('- name: Prepare Windows installer config')
    const signManifest = workflow.indexOf('- name: Sign and stage Runtime manifest')
    const buildInstaller = workflow.indexOf('- name: Build Tauri bundle without publishing')
    expect(prepareConfig).toBeGreaterThan(-1)
    expect(signManifest).toBeLessThan(prepareConfig)
    expect(prepareConfig).toBeLessThan(buildInstaller)
    expect(workflow).toContain('node scripts/windows-installer.mjs --prepare-config')
    expect(workflow).toContain(
      'args: --config src-tauri/tauri.release.conf.json ${{ matrix.tauri_args }}',
    )
  })

  it('never replaces an existing managed Runtime release asset', () => {
    const workflow = readFileSync('.github/workflows/desktop.yml', 'utf8')
    const marker = '- name: Upload or verify immutable Runtime assets'
    const runtimeUpload = workflow.slice(workflow.indexOf(marker))

    expect(runtimeUpload).toContain('gh release upload')
    expect(runtimeUpload).not.toContain('--clobber')
  })

  it('publishes Runtime assets to the immutable managed Runtime tag', () => {
    const workflow = readFileSync('.github/workflows/desktop.yml', 'utf8')
    const release = workflow.slice(workflow.indexOf('  publish:'))

    expect(release).toContain('RUNTIME_TAG="runtime-v${MANAGED_RUNTIME_VERSION}"')
    expect(release).toContain('gh release view "${RUNTIME_TAG}"')
    expect(release).toContain('gh release create "${RUNTIME_TAG}"')
    expect(release).toContain('gh release upload "${RUNTIME_TAG}"')
    expect(release).not.toContain('gh release upload "${{ github.ref_name }}"')
  })

  it('reuses existing immutable Runtime assets before rebuilding them', () => {
    const workflow = readFileSync('.github/workflows/desktop.yml', 'utf8')
    const runtimePreparation = workflow.slice(
      workflow.indexOf('- name: Assemble managed Runtime'),
      workflow.indexOf('- name: Prepare Windows installer config'),
    )

    expect(runtimePreparation).toContain('gh release view "${RUNTIME_TAG}"')
    expect(runtimePreparation).toContain('gh release download "${RUNTIME_TAG}"')
    expect(runtimePreparation).toContain('touch "${RUNTIME_DIR}/.managed-runtime-reused"')
    expect(runtimePreparation).toContain('if test -f "${RUNTIME_DIR}/.managed-runtime-reused"')
  })

  it('can publish managed Runtime assets from a manually dispatched build', () => {
    const workflow = readFileSync('.github/workflows/desktop.yml', 'utf8')
    const release = workflow.slice(workflow.indexOf('  publish:'))

    expect(release).toContain("if: github.event_name != 'pull_request'")
    expect(release).toContain('test "${GITHUB_REF_TYPE}" = \'tag\'')
    expect(release).toContain('test "${GITHUB_REF_NAME}" = "desktop-v${DESKTOP_VERSION}"')
  })

  it('keeps the desktop release version aligned across package manifests', () => {
    const expectedVersion = releaseVersions.desktopVersion
    const packageJson = JSON.parse(readFileSync('package.json', 'utf8')) as { version: string }
    const packageLock = readFileSync('package-lock.json', 'utf8')
    const tauriConfig = JSON.parse(readFileSync('src-tauri/tauri.conf.json', 'utf8')) as { version: string }
    const cargoManifest = readFileSync('src-tauri/Cargo.toml', 'utf8')
    const cargoLock = readFileSync('src-tauri/Cargo.lock', 'utf8')

    expect(packageJson.version).toBe(expectedVersion)
    expect(packageLock).toContain(`"version": "${expectedVersion}"`)
    expect(tauriConfig.version).toBe(expectedVersion)
    expect(cargoManifest).toContain(`version = "${expectedVersion}"`)
    expect(cargoLock).toMatch(
      new RegExp(`name = "deepseek-harness-desktop"\\r?\\nversion = "${expectedVersion}"`),
    )
  })

  it('verifies Runtime assets that already exist in the managed release', () => {
    const workflow = readFileSync('.github/workflows/desktop.yml', 'utf8')
    const release = workflow.slice(workflow.indexOf('  publish:'))

    expect(release).toContain('existing_names=')
    expect(release).toContain('basename "${asset}"')
    expect(release).toContain('gh release download "${RUNTIME_TAG}"')
    expect(release).toContain('cmp -s "${asset}"')
  })

  it('finds Runtime assets below the downloaded artifact directory', () => {
    const workflow = readFileSync('.github/workflows/desktop.yml', 'utf8')
    const release = workflow.slice(workflow.indexOf('  publish:'))

    expect(release).toContain('find release-assets -type f')
    expect(release).not.toContain('find release-assets -maxdepth 1')
  })

  it('always ships a parseable fallback updater configuration', () => {
    const config = JSON.parse(readFileSync('src-tauri/tauri.conf.json', 'utf8')) as {
      plugins?: { updater?: { pubkey?: string, endpoints?: string[] } }
    }
    expect(config.plugins?.updater).toBeTypeOf('object')
    expect(config.plugins?.updater?.pubkey).toMatch(/\S+/)
    expect(config.plugins?.updater?.endpoints).toEqual([
      'https://github.com/XingAur/deepseek-harness-desktop/releases/latest/download/latest.json',
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
    expect(e2eConfig).toContain('npm run agent:build')
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

  it('keeps E2E uninstall data deletion explicitly isolated', () => {
    const uninstall = readFileSync('scripts/e2e/uninstall-web-setup.ps1', 'utf8')
    expect(uninstall).toContain('[switch]$DeleteProjects')
    expect(uninstall).toContain("$arguments += '/DELETEPROJECTS'")
    expect(uninstall).toContain('[System.IO.Path]::GetFullPath([string]$record.dataRoot)')
    expect(uninstall).toContain('.dsh-e2e-owned')
    expect(uninstall).not.toContain('ai.deepseek.harness.desktop\'')
    expect(uninstall).toContain("if ($DeleteAppData -and $DeleteProjects)")
    expect(uninstall).toContain("$sentinel.scope -eq 'app-data'")
    const reset = readFileSync('scripts/e2e/reset-web-setup.ps1', 'utf8')
    const install = readFileSync('scripts/e2e/install-web-setup.ps1', 'utf8')
    const cleanup = readFileSync('scripts/e2e/verify-cleanup.ps1', 'utf8')
    expect(reset).toContain("$BundleId -ne 'ai.deepseek.harness.desktop.e2e'")
    expect(install).toContain("$ProductName -ne 'DeepSeek Harness Desktop E2E'")
    expect(install).toContain('if ($exitCode -eq 0)')
    expect(install).not.toMatch(/Test-Path[^\r\n]*-PathType\s+Container\s+-and\s/)
    expect(uninstall).not.toMatch(/Test-Path[^\r\n]*-PathType\s+Container\s+-and\s/)
    for (const script of [install, reset, uninstall, cleanup]) expect(script).not.toContain('$env:LOCALAPPDATA')
    expect(install).toContain('GetFileName($mainBinary)')
    expect(reset).toContain('. $PSScriptRoot\\owned-tree-cleanup.ps1')
    expect(reset).toContain('Remove-OwnedTreeWithoutFollowingReparsePoints $dataRoot')
    for (const script of [install, reset, uninstall, cleanup]) expect(script).toContain('Assert-NoReparseComponents')
    expect(cleanup).toContain(".TrimEnd('\\') + '\\'")
    expect(cleanup).toContain('ai.deepseek.harness.desktop.e2e')
    expect(cleanup).toContain('Assert-NotReparsePoint')
    expect(readFileSync('src-tauri/src/platform/windows.rs', 'utf8')).toContain('b"E2E-owned"')
    expect(readFileSync('src-tauri/src/platform/windows.rs', 'utf8')).toContain('MetadataExt')
    expect(readFileSync('src-tauri/src/platform/windows.rs', 'utf8')).toContain('FILE_ATTRIBUTE_REPARSE_POINT')
  })

  it('separates quick pull-request and full scheduled installer lifecycles', () => {
    const workflow = readFileSync('.github/workflows/windows-installer-e2e.yml', 'utf8')

    expect(workflow).toContain('pull_request:')
    expect(workflow).toContain('schedule:')
    expect(workflow).toContain('workflow_dispatch:')
    expect(workflow).toContain("'src-tauri/**'")
    expect(workflow).toContain("github.event_name == 'pull_request' && 'quick'")
    expect(workflow).toContain("github.event_name == 'schedule' && 'full'")
    expect(workflow).toContain('inputs.mode')
    expect(workflow).toContain('npm run e2e:setup:quick')
    expect(workflow).toContain('npm run e2e:setup:full')
    expect(workflow).toContain('npm run e2e:installer:quick')
    expect(workflow).toContain('npm run e2e:installer:full')
    expect(workflow).toContain('retention-days: 14')
    expect(workflow).toContain('timeout-minutes: 90')
    expect(workflow).toContain('contents: read')
    expect(workflow).toContain('persist-credentials: false')
    expect(workflow).toContain('npm ci --legacy-peer-deps')
    expect(workflow).toContain('DSH_E2E_ROOT: ${{ github.workspace }}\\\\.dsh-e2e-owned-${{ github.run_id }}')
    expect(workflow).toContain('DSH_E2E_ARTIFACTS: ${{ github.workspace }}\\\\.dsh-e2e-owned-${{ github.run_id }}\\\\e2e-artifacts')
    expect(workflow).not.toMatch(/DSH_E2E_ROOT: \$\{\{ github\.workspace \}\}\\\\\\.dsh-e2e-owned(?:\r?\n|$)/)
    expect(workflow).toContain('- name: Initialize owned E2E root')
    expect(workflow).toContain('initializeOwnedE2EPaths(process.env.DSH_E2E_ROOT, process.env.DSH_E2E_ARTIFACTS)')
    expect(workflow.indexOf('- uses: actions/checkout@v4')).toBeLessThan(workflow.indexOf('- name: Initialize owned E2E root'))
    expect(workflow.indexOf('- name: Initialize owned E2E root')).toBeLessThan(workflow.indexOf('- name: Test deterministic fixtures'))
    expect(workflow).toContain('upload-safe')
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
