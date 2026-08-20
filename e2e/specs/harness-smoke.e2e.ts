import { browser, expect } from '@wdio/globals'
import '@wdio/tauri-service'
import { PackagedDesktopHarness } from '../support/desktop'

describe('packaged tauri harness', () => {
  it('starts the e2e candidate and reads the application version', async () => {
    const desktop = new PackagedDesktopHarness(browser)
    await desktop.launch()

    const version = await browser.tauri.execute(({ core }) => (
      core.invoke('plugin:app|version')
    ))

    await expect(version).toMatch(/^\d+\.\d+\.\d+/)
  })
})
