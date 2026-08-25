#!/usr/bin/env node
// 生成插件市场目录快照：拉取 awesome-dsh-plugin 仓库，解析 data/plugins/*.yml，
// 输出 plugin-catalog/plugins.json（随桌面壳打包为资源）。
// 用法：node scripts/build-plugin-catalog.mjs [--source=main]
import { createHash } from 'node:crypto'
import { mkdirSync, readFileSync, rmSync, writeFileSync, existsSync, readdirSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { execFileSync } from 'node:child_process'
import { tmpdir } from 'node:os'

const SOURCE_REPO = 'awesome-dsh-plugin/awesome-dsh-plugin'
const args = Object.fromEntries(process.argv.slice(2).map((item) => {
  const [key, ...value] = item.replace(/^--/, '').split('=')
  return [key, value.join('=')]
}))
const ref = args.source || 'main'
const output = resolve(args.output || 'plugin-catalog')
const catalogPath = join(output, 'plugins.json')

const work = join(tmpdir(), `dsh-plugin-catalog-${process.pid}`)
rmSync(work, { recursive: true, force: true })
mkdirSync(join(work, 'extract'), { recursive: true })
const archive = join(work, 'catalog.tar.gz')
await download(`https://codeload.github.com/${SOURCE_REPO}/tar.gz/refs/heads/${ref}`, archive)
execFileSync(tarExecutable(), ['-xzf', archive, '-C', join(work, 'extract')])

const root = readdirSync(join(work, 'extract')).find((entry) => entry.startsWith('awesome-dsh-plugin-'))
if (root === undefined) throw new Error('catalog archive layout changed')
const pluginsDir = join(work, 'extract', root, 'data', 'plugins')
const files = readdirSync(pluginsDir).filter((name) => name.endsWith('.yml')).sort()

const entries = []
for (const file of files) {
  const entry = parseEntry(readFileSync(join(pluginsDir, file), 'utf8'))
  if (entry !== null) entries.push(entry)
}
entries.sort((left, right) => left.id.localeCompare(right.id))

const snapshot = {
  schemaVersion: 1,
  source: `https://github.com/${SOURCE_REPO}`,
  sourceRef: ref,
  generatedAt: new Date().toISOString().slice(0, 10),
  count: entries.length,
  entries,
}
mkdirSync(output, { recursive: true })
writeFileSync(catalogPath, `${JSON.stringify(snapshot, null, 1)}\n`)
const sha256 = createHash('sha256').update(readFileSync(catalogPath)).digest('hex')
console.log(`plugin catalog: ${entries.length} entries -> ${catalogPath} (sha256 ${sha256.slice(0, 16)}…)`)

async function download(url, destination) {
  const response = await fetch(url, { redirect: 'error' })
  if (!response.ok) throw new Error(`${url} returned HTTP ${response.status}`)
  writeFileSync(destination, Buffer.from(await response.arrayBuffer()))
}

function tarExecutable() {
  if (process.platform !== 'win32') return 'tar'
  const windowsTar = process.env.SystemRoot ? join(process.env.SystemRoot, 'System32', 'tar.exe') : null
  return windowsTar && existsSync(windowsTar) ? windowsTar : 'tar'
}

/** 只解析市场需要的少数字段；目录条目是简单的 key: value 形式。 */
function parseEntry(text) {
  const fields = {}
  let inDescription = false
  let descriptionLang = null
  for (const rawLine of text.split(/\r?\n/)) {
    if (rawLine.startsWith('description:')) { inDescription = true; continue }
    if (inDescription) {
      const language = /^  (zh|en):/.exec(rawLine)
      if (language) {
        descriptionLang = language[1]
        fields[`description.${descriptionLang}`] = unquote(rawLine.slice(language[0].length))
        continue
      }
      if (/^\S/.test(rawLine)) inDescription = false
      else continue
    }
    const match = /^([a-zA-Z][a-zA-Z0-9_-]*):\s*(.*)$/.exec(rawLine)
    if (match) fields[match[1]] = unquote(match[2])
  }
  const repo = fields.url ?? ''
  // 目录身份以 GitHub HTTPS 仓库为准；安装目标就是该仓库（dsh plugin
  // 是 pnpm 转发器，git URL 可直接安装）。tarball 为可选的发布包直链。
  const repoMatch = /^https:\/\/github\.com\/([A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+)$/.exec(repo)
  if (repoMatch === null) return null
  const tarball = fields.tarball ?? ''
  if (tarball !== '' && !/^https:\/\/github\.com\/[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+\/releases\/(latest\/)?download\/[^\s]+$/.test(tarball)) return null
  const id = repoMatch[1]
  return {
    id,
    displayName: bounded(fields.name, 120).replace(/^["']|["']$/g, '') || id,
    repo,
    category: bounded(fields.category, 40) || 'uncategorized',
    ...(tarball === '' ? {} : { tarball }),
    descriptionZh: bounded(fields['description.zh'], 280),
    descriptionEn: bounded(fields['description.en'], 280),
  }
}

function unquote(value) {
  const trimmed = value.trim()
  if (trimmed.startsWith('"') && trimmed.endsWith('"') && trimmed.length >= 2) return trimmed.slice(1, -1)
  return trimmed
}

function bounded(value, maximum) {
  if (typeof value !== 'string') return ''
  return value.slice(0, maximum)
}
