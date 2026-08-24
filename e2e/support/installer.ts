import { execFile } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { isAbsolute, join, resolve } from 'node:path'
import { promisify } from 'node:util'

const execFileAsync = promisify(execFile)

export interface ProvisioningReceipt {
  runtimeVersion: string
  activeDir: string
  [key: string]: unknown
}

export interface InstallationRecord {
  schemaVersion: number
  installerPath: string
  artifactRoot: string
  installerPid: number
  exitCode: number
  uninstallKey: string
  uninstallString?: string
  uninstallerPath?: string
  installRoot?: string
  appBinary?: string
  shortcuts: readonly string[]
  dataRoot: string
  provisioningReceipt: string
  receipt?: ProvisioningReceipt | null
  desktopPid?: number
  runtimePid?: number
  runtimePort?: number
  completedInstallEntry: boolean
  activeCandidate: boolean
}

export interface PreservationSentinel {
  path: string
  sha256: string
}

export interface PreservationSentinels {
  entries: readonly PreservationSentinel[]
}

export interface InstallerHarness {
  installClean(): Promise<InstallationRecord>
  installExpectingFailure(): Promise<InstallationRecord>
  installWithRetry(): Promise<InstallationRecord>
  reinstallSameVersion(): Promise<InstallationRecord>
  writePreservationSentinels(): Promise<PreservationSentinels>
  uninstallDefault(): Promise<void>
  appBinaryExists(): Promise<boolean>
}

export class WindowsInstallerHarness implements InstallerHarness {
  private latest?: InstallationRecord
  private readonly root: string
  private readonly installer: string
  private readonly artifactRoot: string
  private readonly recordsRoot: string
  private readonly productName: string
  private readonly bundleId: string

  constructor(options: { root?: string; installer?: string; artifactRoot?: string } = {}) {
    this.root = absolute(options.root ?? process.env.DSH_E2E_ROOT, 'DSH_E2E_ROOT')
    this.installer = absolute(options.installer ?? process.env.DSH_E2E_INSTALLER, 'DSH_E2E_INSTALLER')
    this.artifactRoot = absolute(options.artifactRoot ?? process.env.DSH_E2E_ARTIFACT_ROOT, 'DSH_E2E_ARTIFACT_ROOT')
    this.recordsRoot = resolve(process.env.DSH_E2E_ARTIFACTS ?? join(this.root, 'e2e-artifacts'), 'installer-records')
    this.productName = process.env.DSH_E2E_PRODUCT_NAME ?? 'DeepSeek Harness Desktop E2E'
    this.bundleId = process.env.DSH_E2E_BUNDLE_ID ?? 'ai.deepseek.harness.desktop.e2e'
    mkdirSync(this.recordsRoot, { recursive: true })
  }

  async installClean(): Promise<InstallationRecord> {
    await runPowerShell('scripts/e2e/reset-web-setup.ps1', [
      '-ProductName', this.productName,
      '-BundleId', this.bundleId,
    ])
    return this.runInstaller(false)
  }

  installExpectingFailure(): Promise<InstallationRecord> {
    return this.runInstaller(true)
  }

  installWithRetry(): Promise<InstallationRecord> {
    return this.runInstaller(false)
  }

  reinstallSameVersion(): Promise<InstallationRecord> {
    return this.runInstaller(false)
  }

  async writePreservationSentinels(): Promise<PreservationSentinels> {
    const record = this.requireLatest()
    const externalRoot = resolve(this.root, 'e2e-artifacts', 'preserved-project')
    const paths = [
      join(record.dataRoot, 'profiles', 'e2e-preserve.txt'),
      join(record.dataRoot, 'runtime', 'e2e-preserve.txt'),
      join(record.dataRoot, 'state', 'e2e-preserve.txt'),
      join(externalRoot, 'e2e-preserve.txt'),
    ]
    const content = `DeepSeek Harness E2E preservation ${Date.now()}`
    for (const path of paths) {
      mkdirSync(resolve(path, '..'), { recursive: true })
      writeFileSync(path, content, 'utf8')
    }
    const sentinels = {
      entries: paths.map((path) => ({ path, sha256: sha256(readFileSync(path)) })),
    }
    const statePath = join(this.recordsRoot, 'preservation-sentinels.json')
    writeFileSync(statePath, JSON.stringify(sentinels, null, 2), 'utf8')
    return sentinels
  }

  async uninstallDefault(): Promise<void> {
    const record = this.requireLatest()
    const recordPath = join(this.recordsRoot, 'latest-install.json')
    await runPowerShell('scripts/e2e/uninstall-web-setup.ps1', [
      '-RecordPath', recordPath,
      '-SentinelsPath', join(this.recordsRoot, 'preservation-sentinels.json'),
    ])
    this.latest = record
  }

  async appBinaryExists(): Promise<boolean> {
    return this.latest?.appBinary !== undefined && existsSync(this.latest.appBinary)
  }

  private async runInstaller(expectFailure: boolean): Promise<InstallationRecord> {
    const recordPath = join(this.recordsRoot, 'latest-install.json')
    rmSync(recordPath, { force: true })
    let commandFailed = false
    try {
      await runPowerShell('scripts/e2e/install-web-setup.ps1', [
        '-InstallerPath', this.installer,
        '-ArtifactRoot', this.artifactRoot,
        '-RecordPath', recordPath,
        '-ProductName', this.productName,
        '-BundleId', this.bundleId,
      ])
    } catch {
      commandFailed = true
    }
    if (!existsSync(recordPath)) throw new Error('Web Setup did not produce an installation record')
    const record = JSON.parse(readFileSync(recordPath, 'utf8')) as InstallationRecord
    if (expectFailure && !commandFailed && record.exitCode === 0) throw new Error('Web Setup unexpectedly succeeded')
    if (!expectFailure && (commandFailed || record.exitCode !== 0)) {
      throw new Error(`Web Setup failed with exit code ${record.exitCode}`)
    }
    this.latest = record
    return record
  }

  private requireLatest(): InstallationRecord {
    if (this.latest === undefined) throw new Error('No installation record is available')
    return this.latest
  }
}

async function runPowerShell(script: string, args: string[]) {
  await execFileAsync('powershell.exe', [
    '-NoProfile',
    '-NonInteractive',
    '-ExecutionPolicy',
    'RemoteSigned',
    '-File',
    resolve(script),
    ...args,
  ], { windowsHide: true, timeout: 15 * 60_000, maxBuffer: 4 * 1024 * 1024 })
}

function absolute(value: string | undefined, name: string): string {
  if (value === undefined || !isAbsolute(value)) throw new Error(`${name} must be an absolute path`)
  return value
}

function sha256(value: Uint8Array) {
  return createHash('sha256').update(value).digest('hex')
}
