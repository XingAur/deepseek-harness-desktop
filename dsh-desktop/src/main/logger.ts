import { appendFileSync, mkdirSync, readdirSync, rmSync } from 'node:fs'
import { join } from 'node:path'

export interface Logger { (line: string): void }

export function createLogger(logsDir: string, name: string, keepDays: number): Logger {
  mkdirSync(logsDir, { recursive: true })
  prune(logsDir, name, keepDays)
  return function log(line: string) {
    const file = join(logsDir, `${name}-${new Date().toISOString().slice(0, 10)}.log`)
    appendFileSync(file, `${new Date().toISOString()} ${line}\n`, 'utf8')
  }
}

function prune(logsDir: string, name: string, keepDays: number): void {
  const prefix = `${name}-`
  const files = readdirSync(logsDir).filter(f => f.startsWith(prefix)).sort()
  while (files.length > keepDays) rmSync(join(logsDir, files.shift()!), { force: true })
}
