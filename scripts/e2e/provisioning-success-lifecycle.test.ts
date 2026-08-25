import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

describe('quick installer lifecycle scenario ordering', () => {
  it('closes the warm desktop before the next scenario relaunches it to create a project', () => {
    const source = readFileSync(resolve('e2e/specs/provisioning-success.installer.e2e.ts'), 'utf8')
    const firstScenario = source.indexOf("it('installs the desktop shell")
    const secondScenario = source.indexOf("it('creates two sessions")
    const thirdScenario = source.indexOf("it('默认卸载")
    const first = source.slice(firstScenario, secondScenario)
    const second = source.slice(secondScenario, thirdScenario)
    const warmTiming = first.indexOf('const warmTiming')
    const timingOutput = first.indexOf('process.stdout.write')
    const warmQuit = first.indexOf('await desktop.quit()', timingOutput)

    expect(firstScenario).toBeGreaterThan(-1)
    expect(secondScenario).toBeGreaterThan(firstScenario)
    expect(thirdScenario).toBeGreaterThan(secondScenario)
    expect(warmTiming).toBeGreaterThan(-1)
    expect(timingOutput).toBeGreaterThan(warmTiming)
    expect(warmQuit).toBeGreaterThan(timingOutput)
    expect(second.indexOf('await desktop.launch(appBinary)')).toBeGreaterThan(-1)
    expect(second.indexOf('await desktop.waitForWorkbench(8_000)')).toBeGreaterThan(second.indexOf('await desktop.launch(appBinary)'))
    expect(second.indexOf('await desktop.createProject')).toBeGreaterThan(second.indexOf('await desktop.waitForWorkbench(8_000)'))
  })
})
