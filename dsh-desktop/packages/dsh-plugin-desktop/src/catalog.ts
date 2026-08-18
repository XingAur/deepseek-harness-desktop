import { createPublicKey, verify } from 'node:crypto'
import { mkdir, readFile, rename, writeFile } from 'node:fs/promises'
import { dirname } from 'node:path'
import { satisfies, valid } from 'semver'

export const DEV_CATALOG_PUBLIC_KEY = 'cmFlmJvjXIrMN8AbIXxF2c6Gnpt9rDFd_Zhbl0U7AlI'

export type DesktopTarget = 'windows-x86_64' | 'darwin-aarch64'

export interface CatalogPlugin {
  id: string
  packageName: string
  name: string
  description: string
  publisher: string
  repository: string
  installSpec: string
  version: string
  dshRange: string
  platforms: DesktopTarget[]
  verified: true
}

export interface SignedCatalog {
  schemaVersion: 1
  generatedAt: string
  plugins: CatalogPlugin[]
  signature: string
}

export interface CatalogStoreOptions {
  source: string
  cachePath: string
  target: DesktopTarget
  dshVersion: string
  publicKey?: string
  fetcher?: typeof fetch
}

export class CatalogStore {
  constructor(private readonly options: CatalogStoreOptions) {}

  async load(): Promise<SignedCatalog> {
    try {
      const text = await this.readSource()
      const catalog = verifyCatalog(text, this.publicKey())
      await atomicWrite(this.options.cachePath, `${JSON.stringify(catalog, null, 2)}\n`)
      return filterCompatible(catalog, this.options.target, this.options.dshVersion)
    } catch (primary) {
      try {
        const cached = await readFile(this.options.cachePath, 'utf8')
        return filterCompatible(verifyCatalog(cached, this.publicKey()), this.options.target, this.options.dshVersion)
      } catch {
        throw primary
      }
    }
  }

  private async readSource(): Promise<string> {
    if (/^https:\/\//.test(this.options.source)) {
      const response = await (this.options.fetcher ?? fetch)(this.options.source, { redirect: 'error' })
      if (!response.ok) throw new Error(`目录服务返回 HTTP ${response.status}`)
      return response.text()
    }
    if (/^[a-z]+:\/\//i.test(this.options.source)) throw new Error('精选目录只允许 HTTPS 或本地受管文件')
    return readFile(this.options.source, 'utf8')
  }

  private publicKey(): string {
    return this.options.publicKey ?? process.env.DSH_DESKTOP_CATALOG_PUBLIC_KEY ?? DEV_CATALOG_PUBLIC_KEY
  }
}

export function verifyCatalog(text: string, encodedPublicKey: string): SignedCatalog {
  const value: unknown = JSON.parse(text)
  if (!isRecord(value) || value.schemaVersion !== 1 || typeof value.generatedAt !== 'string' || !Array.isArray(value.plugins) || typeof value.signature !== 'string') {
    throw new Error('精选目录结构无效')
  }
  const canonical = Buffer.from(canonicalJson(value, 'signature'))
  const key = createPublicKey({ key: { kty: 'OKP', crv: 'Ed25519', x: encodedPublicKey }, format: 'jwk' })
  if (!verify(null, canonical, key, Buffer.from(value.signature, 'base64url'))) throw new Error('精选目录签名校验失败')
  const plugins = value.plugins.map(parsePlugin)
  return { schemaVersion: 1, generatedAt: value.generatedAt, plugins, signature: value.signature }
}

export function canonicalJson(value: unknown, omittedRootKey = ''): string {
  const sort = (input: unknown, root: boolean): unknown => {
    if (Array.isArray(input)) return input.map((item) => sort(item, false))
    if (!isRecord(input)) return input
    return Object.fromEntries(Object.keys(input).sort().filter((key) => !(root && key === omittedRootKey)).map((key) => [key, sort(input[key], false)]))
  }
  return JSON.stringify(sort(value, true))
}

function parsePlugin(value: unknown): CatalogPlugin {
  if (!isRecord(value)) throw new Error('插件目录项必须是对象')
  const strings = ['id', 'packageName', 'name', 'description', 'publisher', 'repository', 'installSpec', 'version', 'dshRange'] as const
  for (const key of strings) if (typeof value[key] !== 'string' || value[key].length === 0) throw new Error(`插件目录项缺少 ${key}`)
  if (!/^(?:@[a-z0-9._-]+\/)?[a-z0-9._-]+$/i.test(value.packageName as string)) throw new Error('插件包名无效')
  if (!/^https:\/\/github\.com\//i.test(value.repository as string)) throw new Error('插件仓库必须使用 GitHub HTTPS 地址')
  if (value.verified !== true) throw new Error('精选目录不能包含未验证条目')
  if (!Array.isArray(value.platforms) || value.platforms.some((item) => item !== 'windows-x86_64' && item !== 'darwin-aarch64')) throw new Error('插件平台列表无效')
  return value as unknown as CatalogPlugin
}

function filterCompatible(catalog: SignedCatalog, target: DesktopTarget, dshVersion: string): SignedCatalog {
  if (valid(dshVersion) === null) throw new Error('Desktop Runtime 提供的 DSH 版本无效')
  return {
    ...catalog,
    plugins: catalog.plugins.filter((plugin) => plugin.platforms.includes(target) && satisfies(dshVersion, plugin.dshRange, { includePrerelease: true })),
  }
}

function isRecord(value: unknown): value is Record<string, any> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

async function atomicWrite(path: string, content: string) {
  await mkdir(dirname(path), { recursive: true })
  const temporary = `${path}.tmp`
  await writeFile(temporary, content, { encoding: 'utf8', mode: 0o600 })
  await rename(temporary, path)
}
