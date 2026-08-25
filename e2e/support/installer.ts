import { execFile, execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { existsSync, lstatSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { isAbsolute, join, relative, resolve } from 'node:path'
import { promisify } from 'node:util'
import { assertSafeLeaf, assertSafePath, prepareSafeDirectory } from './safe-path'

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
  scope: 'app-data' | 'project' | 'external'
}

export interface PreservationSentinels {
  entries: readonly PreservationSentinel[]
}

export interface InstallerHarness {
  installClean(variant?: InstallerVariantName): Promise<InstallationRecord>
  installOver(variant: InstallerVariantName): Promise<InstallationRecord>
  installExpectingFailure(): Promise<InstallationRecord>
  installWithRetry(): Promise<InstallationRecord>
  reinstallSameVersion(): Promise<InstallationRecord>
  writePreservationSentinels(projectPath?: string): Promise<PreservationSentinels>
  uninstall(mode?: UninstallMode): Promise<void>
  recordRuntimeIdentity(identity: { runtimePid: number; runtimePort: number }): Promise<InstallationRecord>
  cleanupOwnedProject(projectPath: string): void
  cleanupRecordedProcesses(): Promise<void>
  appBinaryExists(): Promise<boolean>
}

export type InstallerVariantName = 'baseline' | 'candidate'
export type UninstallMode = 'preserve-all' | 'delete-app-data' | 'delete-all'
export const E2E_PRODUCT_NAME = 'DeepSeek Harness Desktop E2E'
export const E2E_BUNDLE_ID = 'ai.deepseek.harness.desktop.e2e'

export function selectInstallerPath(explicit: string, installers: InstallerArtifacts | undefined, variant?: InstallerVariantName): string {
  if (variant === undefined) return explicit
  const selected = installers?.[variant]
  if (selected === undefined) throw new Error(`缺少 installers.${variant}`)
  return selected.path
}
export function assertFixedE2eDataRoot(recorded: string, expected: string): void { if (resolve(recorded) !== resolve(expected)) throw new Error('记录 dataRoot 不是固定 E2E 数据根') }

export interface InstallerArtifacts {
  candidate: { path: string; version: string; sha256: string }
  baseline?: { path: string; version: string; sha256: string }
}

export class WindowsInstallerHarness implements InstallerHarness {
  private latest?: InstallationRecord
  private readonly root: string
  private readonly installer: string
  private readonly artifactRoot: string
  private readonly recordsRoot: string
  private readonly productName: string
  private readonly bundleId: string
  private readonly evidenceRoot: string
  private readonly projectsRoot: string
  private readonly dataRoot: string

  readonly installers: InstallerArtifacts | undefined

  constructor(options: { root?: string; installer?: string; artifactRoot?: string; installers?: InstallerArtifacts } = {}) {
    this.root = absolute(options.root ?? process.env.DSH_E2E_ROOT, 'DSH_E2E_ROOT')
    this.installers = options.installers
    this.installer = resolveInstallerPath(options)
    this.artifactRoot = absolute(options.artifactRoot ?? process.env.DSH_E2E_ARTIFACT_ROOT, 'DSH_E2E_ARTIFACT_ROOT')
    this.recordsRoot = resolve(process.env.DSH_E2E_ARTIFACTS ?? join(this.root, 'e2e-artifacts'), 'installer-records')
    this.evidenceRoot = resolve(process.env.DSH_E2E_ARTIFACTS ?? join(this.root, 'e2e-artifacts'))
    this.projectsRoot = resolve(this.root, 'projects-owned')
    this.dataRoot = resolveKnownE2eDataRoot()
    assertSafePath(this.root); assertSafePath(this.artifactRoot); prepareSafeDirectory(this.evidenceRoot); prepareSafeDirectory(this.recordsRoot); prepareSafeDirectory(this.projectsRoot)
    this.productName = E2E_PRODUCT_NAME
    this.bundleId = E2E_BUNDLE_ID
    mkdirSync(this.recordsRoot, { recursive: true })
  }

  async installClean(variant?: InstallerVariantName): Promise<InstallationRecord> {
    await runPowerShell('scripts/e2e/reset-web-setup.ps1', [
      '-ProductName', this.productName,
      '-BundleId', this.bundleId,
    ])
    return this.runInstaller(false, variant)
  }

  installOver(variant: InstallerVariantName): Promise<InstallationRecord> { return this.runInstaller(false, variant) }

  installExpectingFailure(): Promise<InstallationRecord> {
    return this.runInstaller(true)
  }

  installWithRetry(): Promise<InstallationRecord> {
    return this.runInstaller(false)
  }

  reinstallSameVersion(): Promise<InstallationRecord> {
    return this.runInstaller(false)
  }

  async recordRuntimeIdentity(identity: { runtimePid: number; runtimePort: number }): Promise<InstallationRecord> {
    if (!Number.isInteger(identity.runtimePid) || identity.runtimePid <= 0 || !Number.isInteger(identity.runtimePort) || identity.runtimePort < 1 || identity.runtimePort > 65535) throw new Error('runtime identity 无效')
    const record = this.requireLatest()
    assertFixedE2eDataRoot(record.dataRoot, this.dataRoot)
    assertSafePath(record.dataRoot)
    Object.assign(record, identity)
    writeFileSync(join(this.recordsRoot, 'latest-install.json'), JSON.stringify(record, null, 2), 'utf8')
    return record
  }

  async writePreservationSentinels(projectPath = join(this.root, 'projects-owned', 'preserved-project')): Promise<PreservationSentinels> {
    const record = this.requireLatest()
    assertFixedE2eDataRoot(record.dataRoot, this.dataRoot)
    const externalRoot = prepareSafeDirectory(resolve(this.evidenceRoot, 'preserved-external'))
    this.assertOwnedProjectPath(projectPath, false)
    mkdirSync(projectPath, { recursive: true }); assertSafeLeaf(join(projectPath, '.dsh-e2e-project-owned')); writeFileSync(join(projectPath, '.dsh-e2e-project-owned'), 'E2E-owned', 'utf8')
    const paths: Array<{ path: string; scope: PreservationSentinel['scope'] }> = [
      { path: join(record.dataRoot, 'profiles', 'e2e-preserve.txt'), scope: 'app-data' },
      { path: join(projectPath, 'e2e-preserve.txt'), scope: 'project' },
      { path: join(externalRoot, 'e2e-preserve.txt'), scope: 'external' },
    ]
    for (const entry of paths) assertSafePath(entry.path)
    const content = `DeepSeek Harness E2E preservation ${Date.now()}`
    for (const entry of paths) {
      mkdirSync(resolve(entry.path, '..'), { recursive: true })
      assertSafeLeaf(entry.path)
      writeFileSync(entry.path, content, 'utf8')
    }
    const sentinels = {
      entries: paths.map(({ path, scope }) => ({ path, scope, sha256: sha256(readFileSync(path)) })),
    }
    const statePath = join(this.recordsRoot, 'preservation-sentinels.json')
    writeFileSync(statePath, JSON.stringify(sentinels, null, 2), 'utf8')
    return sentinels
  }

  async uninstall(mode: UninstallMode = 'preserve-all'): Promise<void> {
    const record = this.requireLatest()
    const recordPath = join(this.recordsRoot, 'latest-install.json')
    const args = [
      '-RecordPath', recordPath,
      '-SentinelsPath', join(this.recordsRoot, 'preservation-sentinels.json'),
    ]
    if (mode === 'delete-app-data') args.push('-DeleteAppData')
    if (mode === 'delete-all') args.push('-DeleteProjects')
    await runPowerShell('scripts/e2e/uninstall-web-setup.ps1', args)
    this.latest = record
  }

  uninstallDefault(): Promise<void> { return this.uninstall('preserve-all') }

  cleanupOwnedProject(projectPath: string): void {
    const target = resolve(projectPath)
    this.assertOwnedProjectPath(target, true)
    rmSync(target, { recursive: true, force: true })
  }

  private assertOwnedProjectPath(projectPath: string, requireExisting: boolean): void {
    const root = this.projectsRoot
    const target = resolve(projectPath)
    const rel = relative(root, target)
    if (!existsSync(root) || lstatSync(root).isSymbolicLink() || !rel || rel.startsWith('..') || isAbsolute(rel) || (requireExisting && !existsSync(target)) || (requireExisting && !existsSync(join(target, '.dsh-e2e-project-owned')))) throw new Error('拒绝清理非 E2E 所有项目')
    let cursor = root
    for (const part of rel.split(/[\\/]/)) {
      cursor = join(cursor, part)
      if (existsSync(cursor) && lstatSync(cursor).isSymbolicLink()) throw new Error('项目路径包含 reparse point')
    }
  }

  async cleanupRecordedProcesses(): Promise<void> {
    await runPowerShell('scripts/e2e/verify-cleanup.ps1', ['-RecordPath', join(this.recordsRoot, 'latest-install.json'), '-TerminateRecorded'])
  }

  async appBinaryExists(): Promise<boolean> {
    return this.latest?.appBinary !== undefined && existsSync(this.latest.appBinary)
  }

  private async runInstaller(expectFailure: boolean, variant?: InstallerVariantName): Promise<InstallationRecord> {
    const recordPath = join(this.recordsRoot, 'latest-install.json')
    rmSync(recordPath, { force: true })
    let commandFailed = false
    try {
      await runPowerShell('scripts/e2e/install-web-setup.ps1', [
        '-InstallerPath', selectInstallerPath(this.installer, this.installers, variant),
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
    assertWebSetupOutcome(expectFailure, commandFailed, record.exitCode)
    this.latest = record
    return record
  }


  private requireLatest(): InstallationRecord {
    if (this.latest === undefined) throw new Error('No installation record is available')
    return this.latest
  }
}

export function assertWebSetupOutcome(expectFailure: boolean, commandFailed: boolean, installerExitCode: number): void {
  if (expectFailure && !commandFailed && installerExitCode === 0) throw new Error('Web Setup unexpectedly succeeded')
  if (!expectFailure && commandFailed && installerExitCode === 0) {
    // execFile rejects only a failed PowerShell process; normal stderr does not
    // set commandFailed.  Keep that script failure distinct from the NSIS
    // installer result recorded before the script's final validation.
    throw new Error('Web Setup PowerShell validation failed after installer exit code 0')
  }
  if (!expectFailure && (commandFailed || installerExitCode !== 0)) {
    throw new Error(`Web Setup failed with exit code ${installerExitCode}`)
  }
}

function resolveKnownE2eDataRoot(): string {
  if (process.platform !== 'win32') return resolve(process.cwd(), 'ai.deepseek.harness.desktop.e2e')
  // Unit tests construct the harness on Windows without a real installer
  // run; LOCALAPPDATA holds the same SpecialFolder value the PowerShell
  // probe returns and is always set on Windows runners and desktops.
  const localAppData = process.env.LOCALAPPDATA
  if (localAppData !== undefined && localAppData !== '' && isAbsolute(localAppData)) {
    return resolve(localAppData, E2E_BUNDLE_ID)
  }
  const local = execFileSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', '[Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)'], { encoding: 'utf8' }).trim()
  if (!isAbsolute(local)) throw new Error('无法确定 Windows LocalApplicationData')
  return resolve(local, E2E_BUNDLE_ID)
}

export function resolveInstallerPath(options: { installer?: string; installers?: InstallerArtifacts } = {}): string {
  return absolute(options.installer ?? process.env.DSH_E2E_INSTALLER ?? options.installers?.candidate.path, 'DSH_E2E_INSTALLER')
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
