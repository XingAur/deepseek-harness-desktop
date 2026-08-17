import { execSync } from 'node:child_process'
import { cpSync, mkdirSync, rmSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const DSH_VER = process.env.DSH_VERSION ?? '0.1.0-rc.6'
const PNPM_VER = process.env.PNPM_VERSION ?? '11.22.0'
const REG = process.env.NPM_REGISTRY ?? 'https://registry.npmmirror.com'

rmSync('build', { recursive: true, force: true })
rmSync('resources', { recursive: true, force: true })

async function vendor(pkg, outDir) {
  const stage = join('build', outDir.replaceAll(/[\\/]/g, '-'))
  mkdirSync(stage, { recursive: true })
  writeFileSync(join(stage, 'package.json'), JSON.stringify({ name: 'stage', private: true }))
  execSync(`npm install ${pkg} --omit=dev --registry ${REG} --no-audit --no-fund --ignore-scripts`, { cwd: stage, stdio: 'inherit' })
  mkdirSync(outDir, { recursive: true })
  cpSync(join(stage, 'node_modules'), join(outDir, 'node_modules'), { recursive: true })
  console.log(`vendored ${pkg} -> ${outDir}`)
}

await vendor(`@deepseek-ai/dsh@${DSH_VER}`, join('resources', 'dsh'))
await vendor(`pnpm@${PNPM_VER}`, join('resources', 'runtime-pnpm'))
