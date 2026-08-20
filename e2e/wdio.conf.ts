import { existsSync, mkdirSync } from 'node:fs'
import { isAbsolute, resolve } from 'node:path'
import '@wdio/tauri-service'
import type { TauriCapabilities } from '@wdio/tauri-service'

const configuredBinary = process.env.DSH_E2E_APP_BINARY
if (configuredBinary === undefined || configuredBinary.trim() === '') {
  throw new Error('DSH_E2E_APP_BINARY 必须指向启用 e2e feature 的候选应用')
}
if (!isAbsolute(configuredBinary) || !existsSync(configuredBinary)) {
  throw new Error(`DSH_E2E_APP_BINARY 必须是存在的绝对文件：${configuredBinary}`)
}

const artifacts = resolve(process.env.DSH_E2E_ARTIFACTS ?? 'e2e-artifacts')
mkdirSync(artifacts, { recursive: true })
const installerSuite = process.env.DSH_E2E_SUITE === 'installer'

export const config: WebdriverIO.Config = {
  runner: 'local',
  specs: ['./specs/**/*.e2e.ts'],
  maxInstances: 1,
  logLevel: 'info',
  bail: 0,
  waitforTimeout: 120_000,
  connectionRetryTimeout: 120_000,
  connectionRetryCount: 1,
  framework: 'mocha',
  reporters: [['spec', { addConsoleLogs: true }]],
  mochaOpts: {
    ui: 'bdd',
    timeout: installerSuite ? 15 * 60_000 : 120_000,
  },
  capabilities: [{
    browserName: 'tauri',
    'tauri:options': { application: configuredBinary },
  } as TauriCapabilities],
  services: [[
    '@wdio/tauri-service',
    {
      appBinaryPath: configuredBinary,
      driverProvider: 'embedded',
      captureBackendLogs: true,
      captureFrontendLogs: true,
      logDir: artifacts,
      env: e2eEnvironment(),
    },
  ]],
  outputDir: artifacts,
  afterTest: async (_test, _context, result) => {
    if (result.passed) return
    const safeName = `${Date.now()}-failure.png`
    await browser.saveScreenshot(resolve(artifacts, safeName))
  },
}

function e2eEnvironment() {
  return Object.fromEntries(Object.entries(process.env).filter(
    ([name, value]) => (
      name.startsWith('DSH_E2E_')
      || name.startsWith('DSH_DESKTOP_E2E_')
      || name === 'NODE_EXTRA_CA_CERTS'
    ) && value !== undefined,
  )) as Record<string, string>
}
