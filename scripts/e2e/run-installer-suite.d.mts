export type InstallerSuiteMode = 'quick' | 'full'

export interface InstallerSuiteCommand {
  command: string
  args: string[]
  options: {
    cwd: string
    env: NodeJS.ProcessEnv
    stdio: 'inherit'
    windowsHide: true
  }
}

export function parseInstallerSuiteMode(args: readonly string[]): InstallerSuiteMode
export function createInstallerSuiteCommand(mode: InstallerSuiteMode, cwd?: string, env?: NodeJS.ProcessEnv): InstallerSuiteCommand
export function assertInstallerSuiteReady(
  mode: InstallerSuiteMode,
  options?: {
    cwd?: string
    env?: NodeJS.ProcessEnv
    readFile?: (path: string, encoding: string) => string | Buffer
    exists?: (path: string) => boolean
  },
): void
export function runInstallerSuite(
  mode: InstallerSuiteMode,
  options?: {
    cwd?: string
    env?: NodeJS.ProcessEnv
    readFile?: (path: string, encoding: string) => string | Buffer
    exists?: (path: string) => boolean
    spawnProcess?: (...args: unknown[]) => { once(event: string, listener: (...args: any[]) => unknown): unknown }
  },
): Promise<number>
