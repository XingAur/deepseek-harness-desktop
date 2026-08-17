import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

export interface ChildEnvPaths { binDir: string; dshHome: string }

export function buildChildEnv(paths: ChildEnvPaths, extra: NodeJS.ProcessEnv = {}): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env, ...extra }
  env.ELECTRON_RUN_AS_NODE = '1'
  env.DSH_HOME = paths.dshHome
  env.PATH = `${paths.binDir};${env.PATH ?? ''}`
  if (process.env.npm_config_registry) env.npm_config_registry = process.env.npm_config_registry
  else if (!env.npm_config_registry) env.npm_config_registry = 'https://registry.npmmirror.com'
  return env
}

export function ensurePnpmShim(binDir: string, execPath: string, pnpmJs: string): void {
  mkdirSync(binDir, { recursive: true })
  const cmd = [
    '@echo off',
    'set ELECTRON_RUN_AS_NODE=1',
    `"${execPath}" "${pnpmJs}" %*`,
    '',
  ].join('\r\n')
  writeFileSync(join(binDir, 'pnpm.cmd'), cmd, 'utf8')
}
