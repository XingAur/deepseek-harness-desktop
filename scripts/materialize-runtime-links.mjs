import {
  chmodSync,
  copyFileSync,
  existsSync,
  lstatSync,
  mkdirSync,
  readdirSync,
  readlinkSync,
  realpathSync,
  renameSync,
  rmSync,
} from 'node:fs'
import { basename, dirname, isAbsolute, join, relative, resolve, sep } from 'node:path'

export function materializeRuntimeLinks(stageDirectory, buildRoot = stageDirectory) {
  const stage = resolve(stageDirectory)
  const root = realpathSync(resolve(buildRoot))
  visitStage(stage, root)
}

function visitStage(entryPath, buildRoot) {
  const metadata = lstatSync(entryPath)
  if (metadata.isSymbolicLink()) {
    replaceLink(entryPath, buildRoot)
    return
  }
  if (metadata.isDirectory()) {
    for (const child of readdirSync(entryPath)) {
      visitStage(join(entryPath, child), buildRoot)
    }
    return
  }
  if (!metadata.isFile()) {
    throw new Error(`Runtime 构建目录包含不支持的特殊文件：${entryPath}`)
  }
}

function replaceLink(linkPath, buildRoot) {
  const target = resolveLinkTarget(linkPath, buildRoot)
  const temporary = join(
    dirname(linkPath),
    `.${basename(linkPath)}.materialize-${process.pid}`,
  )
  if (existsSync(temporary)) {
    throw new Error(`Runtime 符号链接临时文件已存在：${temporary}`)
  }
  copyEntry(target, temporary, buildRoot, new Set())
  rmSync(linkPath, { recursive: true, force: true })
  renameSync(temporary, linkPath)
}

function copyEntry(sourcePath, destinationPath, buildRoot, activeDirectories) {
  const metadata = lstatSync(sourcePath)
  if (metadata.isSymbolicLink()) {
    return copyEntry(
      resolveLinkTarget(sourcePath, buildRoot),
      destinationPath,
      buildRoot,
      activeDirectories,
    )
  }
  if (metadata.isDirectory()) {
    const realPath = realpathSync(sourcePath)
    if (activeDirectories.has(realPath)) {
      throw new Error(`Runtime 符号链接形成目录循环：${sourcePath}`)
    }
    const nextActiveDirectories = new Set(activeDirectories)
    nextActiveDirectories.add(realPath)
    mkdirSync(destinationPath, { recursive: true })
    for (const child of readdirSync(sourcePath)) {
      copyEntry(
        join(sourcePath, child),
        join(destinationPath, child),
        buildRoot,
        nextActiveDirectories,
      )
    }
    chmodSync(destinationPath, metadata.mode & 0o7777)
    return
  }
  if (!metadata.isFile()) {
    throw new Error(`Runtime 符号链接指向不支持的特殊文件：${sourcePath}`)
  }
  mkdirSync(dirname(destinationPath), { recursive: true })
  copyFileSync(sourcePath, destinationPath)
  chmodSync(destinationPath, metadata.mode & 0o7777)
}

function resolveLinkTarget(linkPath, buildRoot) {
  const targetPath = resolve(dirname(linkPath), readlinkSync(linkPath))
  let realTarget
  try {
    realTarget = realpathSync(targetPath)
  } catch {
    throw new Error(`Runtime 符号链接目标不存在：${linkPath} -> ${targetPath}`)
  }
  const relativeTarget = relative(buildRoot, realTarget)
  if (
    relativeTarget
    && (
      relativeTarget === '..'
      || relativeTarget.startsWith(`..${sep}`)
      || isAbsolute(relativeTarget)
    )
  ) {
    throw new Error(`Runtime 符号链接指向构建目录之外：${linkPath} -> ${realTarget}`)
  }
  return realTarget
}
