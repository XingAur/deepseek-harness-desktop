import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterAll, describe, expect, it } from 'vitest'
import { bundledPythonExecutable, pythonAssetName, pythonDownloadUrl, pythonEnvironmentRoot } from './assemble-harness-core.mjs'
import { embeddedPythonCandidates } from './harness-host.mjs'

const directories: string[] = []
function temporary() {
  const directory = mkdtempSync(join(tmpdir(), 'assemble-core-'))
  directories.push(directory)
  return directory
}

afterAll(() => {
  for (const directory of directories) rmSync(directory, { recursive: true, force: true })
})

describe('harness core assembly', () => {
  it('pins a relocatable python for every packaging platform', () => {
    expect(pythonAssetName('darwin', 'arm64')).toContain('aarch64-apple-darwin-install_only.tar.gz')
    expect(pythonAssetName('win32', 'x64')).toContain('x86_64-pc-windows-msvc-install_only.tar.gz')
    expect(() => pythonAssetName('linux', 'x64')).toThrow(/没有预置/)
    const url = pythonDownloadUrl('darwin', 'arm64')
    expect(url).toContain('python-build-standalone/releases/download/')
    expect(url).toContain(encodeURIComponent('+'))
  })

  it('prefers the bundled runtime layout and falls back to a venv layout', () => {
    const coreRoot = temporary()
    expect(bundledPythonExecutable(coreRoot, 'darwin')).toBe('')
    mkdirSync(join(coreRoot, 'runtime', 'bin'), { recursive: true })
    writeFileSync(join(coreRoot, 'runtime', 'bin', 'python3'), '#!/bin/sh\n')
    expect(bundledPythonExecutable(coreRoot, 'darwin').endsWith(join('runtime', 'bin', 'python3'))).toBe(true)

    const venvRoot = temporary()
    mkdirSync(join(venvRoot, '.venv', 'bin'), { recursive: true })
    writeFileSync(join(venvRoot, '.venv', 'bin', 'python'), '#!/bin/sh\n')
    expect(bundledPythonExecutable(venvRoot, 'darwin').endsWith(join('.venv', 'bin', 'python'))).toBe(true)
  })

  it('keeps dependency markers beside both Unix and Windows bundled runtimes', () => {
    const coreRoot = '/opt/harness/core'
    expect(pythonEnvironmentRoot(coreRoot, '/opt/harness/core/runtime/bin/python3')).toBe('/opt/harness/core/runtime')
    const windowsRoot = 'C:\\Harness\\core'
    expect(pythonEnvironmentRoot(windowsRoot, 'C:\\Harness\\core\\runtime\\python.exe')).toBe(join(windowsRoot, 'runtime'))
    expect(pythonEnvironmentRoot(coreRoot, '/opt/harness/core/.venv/bin/python')).toBe('/opt/harness/core/.venv')
  })

  it('offers platform specific embedded python candidates for the host entrypoint', () => {
    const darwin = embeddedPythonCandidates('/opt/harness/core', 'darwin').map((candidate) => candidate.replaceAll('\\', '/'))
    expect(darwin[0].endsWith('runtime/bin/python3')).toBe(true)
    expect(darwin.some((candidate) => candidate.endsWith('.venv/bin/python'))).toBe(true)
    const windows = embeddedPythonCandidates('C:\\Harness\\core', 'win32').map((candidate) => candidate.replaceAll('\\', '/'))
    expect(windows[0].endsWith('runtime/python.exe')).toBe(true)
    expect(windows.some((candidate) => candidate.endsWith('.venv/Scripts/python.exe'))).toBe(true)
    expect(embeddedPythonCandidates('')).toEqual([])
  })
})
