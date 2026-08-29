#!/usr/bin/env node
/**
 * 组装打进安装包的 harness/core 资源目录（build/harness-core）。
 *
 * 产物布局：
 *   build/harness-core/            ← vendor/harness-core 的代码副本
 *   build/harness-core/runtime/    ← 可重定位的 python-build-standalone
 *
 * 关键点：普通 venv 不可重定位（绝对路径写死在 pyvenv.cfg/脚本里，CI 构建
 * 机的路径在用户机器上不存在），因此使用 python-build-standalone 的
 * install_only 发行版——它专为重定位设计，随 .app/NSIS 安装到任意路径都能
 * 运行。tarball 下载到 build/cache 复用；重复执行幂等。
 *
 * 环境变量覆盖：
 *   DSH_HARNESS_PYTHON_TARBALL  本地 tar.gz 路径（离线构建/CI 缓存）
 *   DSH_HARNESS_PYTHON_URL      自定义下载地址
 *   DSH_HARNESS_SKIP_PYTHON=1   只拷贝代码（本地快速迭代）
 */

import { createHash } from 'node:crypto'
import { spawnSync } from 'node:child_process'
import { cpSync, existsSync, mkdirSync, readFileSync, renameSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { copyHarnessCore, syncVendorFromSource } from './vendor-harness-core.mjs'

export const PBS_TAG = '20260825'
export const PBS_PYTHON_VERSION = 'cpython-3.12.14'
const PBS_BASE = `https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_TAG}`

const PLATFORM_ASSETS = {
  'darwin-arm64': `${PBS_PYTHON_VERSION}+${PBS_TAG}-aarch64-apple-darwin-install_only.tar.gz`,
  'darwin-x64': `${PBS_PYTHON_VERSION}+${PBS_TAG}-x86_64-apple-darwin-install_only.tar.gz`,
  'win32-x64': `${PBS_PYTHON_VERSION}+${PBS_TAG}-x86_64-pc-windows-msvc-install_only.tar.gz`,
}

export function pythonAssetName(platform = process.platform, arch = process.arch) {
  const asset = PLATFORM_ASSETS[`${platform}-${arch}`]
  if (asset === undefined) {
    throw new Error(`当前平台没有预置的 Harness Python 发行版：${platform}-${arch}`)
  }
  return asset
}

export function pythonDownloadUrl(platform = process.platform, arch = process.arch) {
  return `${PBS_BASE}/${pythonAssetName(platform, arch).replaceAll('+', '%2B')}`
}

export function bundledPythonExecutable(coreRoot, platform = process.platform) {
  const candidates = platform === 'win32'
    ? [join(coreRoot, 'runtime', 'python.exe'), join(coreRoot, '.venv', 'Scripts', 'python.exe')]
    : [join(coreRoot, 'runtime', 'bin', 'python3'), join(coreRoot, '.venv', 'bin', 'python')]
  return candidates.find((candidate) => existsSync(candidate)) ?? ''
}

export function pythonEnvironmentRoot(coreRoot, executable) {
  const runtimeRoot = join(coreRoot, 'runtime')
  const normalizedRuntimeRoot = runtimeRoot.replaceAll('\\', '/')
  const normalizedExecutable = executable.replaceAll('\\', '/')
  return normalizedExecutable.startsWith(`${normalizedRuntimeRoot}/`)
    ? runtimeRoot
    : join(coreRoot, '.venv')
}

function probePython(executable) {
  if (executable === '' || !existsSync(executable)) return false
  const probe = spawnSync(executable, ['-c', 'import sys; print(sys.version_info[0])'], {
    encoding: 'utf8',
    timeout: 30_000,
    windowsHide: true,
  })
  return probe.status === 0 && probe.stdout.trim() === '3'
}

function isIntactTarball(tarball) {
  const check = spawnSync('tar', ['-tzf', tarball], { stdio: 'ignore', timeout: 120_000 })
  return check.status === 0
}

/** 断点续传式下载：代理/CI 网络不稳定时按块续传，直到 tar 校验通过。 */
function download(url, target) {
  process.stdout.write(`下载 Harness Python：${url}\n`)
  for (let attempt = 1; attempt <= 20; attempt += 1) {
    if (existsSync(target) && isIntactTarball(target)) return
    const response = spawnSync(
      'curl',
      ['-fsSL', '--http1.1', '--max-time', '300', '--connect-timeout', '30', '-C', '-', '-o', target, url],
      { stdio: 'inherit', timeout: 330_000 },
    )
    if (response.status === 0 && existsSync(target) && isIntactTarball(target)) return
    process.stdout.write(`下载中断（curl 退出码 ${response.status ?? 'signal'}），第 ${attempt} 次续传…\n`)
  }
  throw new Error('Harness Python 下载失败：多次续传后仍未完成')
}

function extractTarball(tarball, directory) {
  mkdirSync(directory, { recursive: true })
  const extracted = spawnSync('tar', ['-xzf', tarball, '-C', directory], { stdio: 'inherit', timeout: 300_000 })
  if (extracted.status !== 0) throw new Error(`Harness Python 解压失败（tar 退出码 ${extracted.status}）`)
}

function installRequirements(executable, environmentRoot, requirementsPath) {
  const markerPath = join(environmentRoot, '.deps-installed')
  const requirementsHash = existsSync(requirementsPath)
    ? createHash('sha256').update(readFileSync(requirementsPath)).digest('hex')
    : ''
  if (requirementsHash === '') return
  if (existsSync(markerPath) && readFileSync(markerPath, 'utf8').trim() === requirementsHash) return
  const ensure = spawnSync(executable, ['-m', 'ensurepip', '--upgrade'], { stdio: 'ignore', timeout: 120_000, windowsHide: true })
  if (ensure.status !== 0) process.stdout.write('提示：ensurepip 不可用，尝试直接使用内置 pip\n')
  const install = spawnSync(
    executable,
    ['-m', 'pip', 'install', '--no-cache-dir', '--quiet', '--disable-pip-version-check', '-r', requirementsPath],
    { stdio: 'inherit', timeout: 600_000, windowsHide: true },
  )
  if (install.status !== 0) throw new Error(`Harness Python 依赖安装失败（pip 退出码 ${install.status}）`)
  writeFileSync(markerPath, `${requirementsHash}\n`)
}

/**
 * 本机回退：用系统 Python 在 build/harness-core/.venv 建独立环境。
 * `--copies` 确保解释器是真实文件拷贝（可进安装包资源）；但 venv 的标准库
 * 仍指向构建机的 Python 安装路径，因此产物只适用于与构建机相同 Python
 * 路径的机器（即本机自用构建）。CI 与分发构建必须使用可重定位发行版。
 */
function assembleSystemVenv(out) {
  const venvDir = join(out, '.venv')
  const executable = process.platform === 'win32'
    ? join(venvDir, 'Scripts', 'python.exe')
    : join(venvDir, 'bin', 'python')
  if (!probePython(executable)) {
    rmSync(venvDir, { recursive: true, force: true })
    const systemPython = process.env.DSH_HARNESS_SYSTEM_PYTHON_BIN || 'python3'
    const created = spawnSync(systemPython, ['-m', 'venv', '--copies', venvDir], { stdio: 'inherit', timeout: 300_000 })
    if (created.status !== 0) {
      throw new Error(
        `系统 Python venv 创建失败（${systemPython}）。CommandLineTools 自带的 python3 不支持 --copies，`
        + '请设置 DSH_HARNESS_SYSTEM_PYTHON_BIN 指向 Homebrew Python（如 /opt/homebrew/bin/python3.14）',
      )
    }
  }
  if (!probePython(executable)) throw new Error(`系统 Python venv 不可执行：${executable}`)
  installRequirements(executable, venvDir, join(out, 'requirements.txt'))
  process.stdout.write('警告：--system-python 产物依赖构建机的 Python 安装路径，只适合本机自用；CI/分发构建请使用默认的可重定位发行版。\n')
  return executable
}

async function main() {
  const repositoryRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')
  const vendor = join(repositoryRoot, 'vendor', 'harness-core')
  // 构建前自动同步本机 Harness 源码（无需记忆手动命令）；CI/无源目录时跳过。
  const sync = syncVendorFromSource(repositoryRoot)
  if (sync.synced) {
    process.stdout.write(
      sync.changed
        ? `Harness 源码已变更，自动同步到 vendor/harness-core（${sync.fileCount} 个文件，来源 ${sync.source}）\n`
        : `vendor 副本与 Harness 源码一致（${sync.source}）\n`,
    )
  } else if (sync.reason === 'source-unavailable') {
    process.stdout.write('未找到本机 Harness 源目录，使用仓库内 vendor 副本（CI 常态）\n')
  }
  if (!existsSync(vendor)) {
    throw new Error('缺少 vendor/harness-core 且本机没有可同步的 Harness 源目录')
  }
  const out = join(repositoryRoot, 'build', 'harness-core')
  // 保留已安装的 Python 运行时与组装清单，只刷新 Core 代码，重复构建保持幂等高效。
  const copied = copyHarnessCore(vendor, out, { preserve: ['runtime', '.venv', 'BUNDLE_MANIFEST.json'] })
  process.stdout.write(`已拷贝 Core 代码：${copied.fileCount} 个文件\n`)

  let python = { bundled: false, executable: '', asset: '' }
  if (process.env.DSH_HARNESS_SKIP_PYTHON !== '1') {
    const useSystemVenv = process.argv.includes('--system-python') || process.env.DSH_HARNESS_SYSTEM_PYTHON === '1'
    const requirementsPath = join(out, 'requirements.txt')
    let executable
    let asset
    if (useSystemVenv) {
      executable = assembleSystemVenv(out)
      asset = 'system-venv'
    } else {
      const runtimeDir = join(out, 'runtime')
      executable = bundledPythonExecutable(out)
      if (!probePython(executable)) {
        const assetName = pythonAssetName()
        const cacheDir = join(repositoryRoot, 'build', 'cache')
        mkdirSync(cacheDir, { recursive: true })
        const tarball = process.env.DSH_HARNESS_PYTHON_TARBALL && existsSync(process.env.DSH_HARNESS_PYTHON_TARBALL)
          ? process.env.DSH_HARNESS_PYTHON_TARBALL
          : join(cacheDir, assetName)
        if (!existsSync(tarball) || !isIntactTarball(tarball)) {
          download(process.env.DSH_HARNESS_PYTHON_URL || pythonDownloadUrl(), tarball)
        }
        const staging = join(repositoryRoot, 'build', '.harness-python-extract')
        rmSync(staging, { recursive: true, force: true })
        extractTarball(tarball, staging)
        // install_only 包的根目录固定是 python/
        const inner = join(staging, 'python')
        if (!existsSync(inner)) throw new Error('Harness Python 包结构异常：缺少 python/ 根目录')
        rmSync(runtimeDir, { recursive: true, force: true })
        // 必须用 rename 移动而不是 cpSync 拷贝：发行版内的 python3 等是相对符号链接，
        // cpSync 会把链接目标改写成暂存目录的绝对路径，移动后即悬空。
        renameSync(inner, runtimeDir)
        rmSync(staging, { recursive: true, force: true })
        executable = bundledPythonExecutable(out)
      }
      if (!probePython(executable)) throw new Error(`Harness Python 不可执行：${executable}`)
      // marker 跟随解释器实际所在环境（runtime/ 或遗留 .venv/），避免分支错配。
      const environmentRoot = pythonEnvironmentRoot(out, executable)
      installRequirements(executable, environmentRoot, requirementsPath)
      asset = pythonAssetName()
    }
    python = { bundled: true, executable, asset }
  }

  const manifest = {
    schema: 'harness-core-bundle.v1',
    generatedAt: new Date().toISOString(),
    fileCount: copied.fileCount,
    manifestSha256: copied.manifestSha256,
    python,
  }
  writeFileSync(join(out, 'BUNDLE_MANIFEST.json'), `${JSON.stringify(manifest, null, 2)}\n`)
  process.stdout.write(`harness/core 资源组装完成 → ${out}\n${JSON.stringify(manifest, null, 2)}\n`)
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  await main()
}
