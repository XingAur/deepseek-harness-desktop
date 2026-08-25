import { lstat, readFile, readdir, rm, rmdir, unlink } from 'node:fs/promises'
import { isAbsolute, relative, resolve, sep } from 'node:path'
import { pathToFileURL } from 'node:url'

const OWNERSHIP_MARKER = '.dsh-e2e-owned'

export async function removeOwnedTreeWithoutFollowingReparsePoints(root, { beforeEnumerate, beforeScheduleDirectChildren, onLstat, onOperationEnd, onOperationStart } = {}) {
  const rootPath = resolve(root)
  const context = {
    beforeEnumerate,
    beforeScheduleDirectChildren,
    onLstat,
    onOperationEnd,
    onOperationStart,
    rootPath,
    semaphore: new Semaphore(16),
  }
  await refreshOwnedRootMarker(context)
  await removeEntry(context, rootPath)
}

async function removeEntry(context, path, precheckedMetadata) {
  // A reparse work item may be removed as a leaf only.  The root and every
  // ancestor are always rechecked before a traversal or normal deletion.
  const metadata = precheckedMetadata ?? await assertOwnedTreePathWithoutReparsePoints(context, path, true)
  if (metadata.isSymbolicLink()) {
    const current = await assertOwnedTreePathWithoutReparsePoints(context, path, true)
    if (!current.isSymbolicLink()) return removeEntry(context, path)
    await removeLinkEntry(context, path)
    return
  }
  if (!metadata.isDirectory()) {
    await assertOwnedTreePathWithoutReparsePoints(context, path, false)
    await limited(context, () => unlink(path))
    return
  }

  await assertOwnedTreePathWithoutReparsePoints(context, path, false)
  if (path === context.rootPath) await refreshOwnedRootMarker(context)
  if (context.beforeEnumerate !== undefined) await context.beforeEnumerate(path)
  await assertOwnedTreePathWithoutReparsePoints(context, path, false)
  const entries = await limited(context, () => readdir(path))
  await assertOwnedTreePathWithoutReparsePoints(context, path, false)
  if (path === context.rootPath) await refreshOwnedRootMarker(context)
  // Preserve the reset authorization marker until all payload entries have
  // been removed, so an interruption never leaves a substantial unmarked tree.
  const marker = path === context.rootPath ? context.markerName : undefined
  const children = entries.filter((entry) => entry !== marker)
  const precheckedChildren = await checkDirectChildren(context, path, children)
  await Promise.all(precheckedChildren.map(({ path: childPath, metadata: childMetadata }) => removeEntry(context, childPath, childMetadata)))
  if (path === context.rootPath) await refreshOwnedRootMarker(context)
  if (marker !== undefined && entries.includes(marker)) await removeEntry(context, resolve(path, marker))
  await assertOwnedTreePathWithoutReparsePoints(context, path, false)
  await limited(context, () => rmdir(path))
}

async function assertOwnedTreePathWithoutReparsePoints(context, path, allowLeafReparse) {
  const { rootPath } = context
  const candidate = resolve(path)
  if (!isOwnedDescendant(rootPath, candidate)) throw new Error('拒绝遍历 owned root 外的路径')
  let cursor = rootPath
  const components = relative(rootPath, candidate).split(/[\\/]/).filter(Boolean)
  const rootMetadata = await checkedLstat(context, cursor)
  if (rootMetadata.isSymbolicLink()) throw new Error(`拒绝遍历 reparse point: ${cursor}`)
  for (const component of components) {
    cursor = resolve(cursor, component)
    const metadata = await checkedLstat(context, cursor)
    if (metadata.isSymbolicLink() && (!allowLeafReparse || cursor !== candidate)) {
      throw new Error(`拒绝遍历 reparse point: ${cursor}`)
    }
    if (cursor === candidate) return metadata
  }
  return rootMetadata
}

async function checkDirectChildren(context, parent, entries) {
  // Do not issue I/O for a descendant until its immediate parent has been
  // verified.  A hook then models replacement in this narrow race window;
  // the second parent check must fail before any child lstat is scheduled.
  await assertOwnedTreePathWithoutReparsePoints(context, parent, false)
  if (context.beforeScheduleDirectChildren !== undefined) await context.beforeScheduleDirectChildren(parent)
  await assertOwnedTreePathWithoutReparsePoints(context, parent, false)
  return Promise.all(entries.map(async (entry) => {
    const child = resolve(parent, entry)
    return { path: child, metadata: await checkedLstat(context, child) }
  }))
}

async function checkedLstat(context, path) {
  context.onLstat?.(path)
  return limited(context, () => lstat(path))
}

function isOwnedDescendant(root, candidate) {
  const relation = relative(root, candidate)
  return relation === '' || (!relation.startsWith(`..${sep}`) && relation !== '..' && !isAbsolute(relation))
}

async function assertOwnedRootMarker(context) {
  const rootBefore = await limited(context, () => lstat(context.rootPath))
  if (!rootBefore.isDirectory() || rootBefore.isSymbolicLink()) throw new Error('E2E data root ownership marker is unsafe')
  const entries = await limited(context, () => readdir(context.rootPath))
  const markerName = entries.find((entry) => isOwnershipMarkerName(entry))
  if (markerName === undefined) throw new Error('E2E data root lacks .dsh-e2e-owned ownership marker')
  const markerPath = resolve(context.rootPath, markerName)
  const markerBefore = await limited(context, () => lstat(markerPath))
  if (!markerBefore.isFile() || markerBefore.isSymbolicLink()) throw new Error('E2E data root ownership marker is unsafe')
  const contents = await limited(context, () => readFile(markerPath, 'utf8'))
  const rootAfter = await limited(context, () => lstat(context.rootPath))
  const markerAfter = await limited(context, () => lstat(markerPath))
  if (!rootAfter.isDirectory() || rootAfter.isSymbolicLink() || !sameFile(rootBefore, rootAfter) || !markerAfter.isFile() || markerAfter.isSymbolicLink() || !sameFile(markerBefore, markerAfter) || contents !== 'E2E-owned') {
    throw new Error('E2E data root ownership marker is invalid')
  }
  return markerName
}

async function refreshOwnedRootMarker(context) {
  context.markerName = await assertOwnedRootMarker(context)
}

function isOwnershipMarkerName(entry) {
  return process.platform === 'win32' ? entry.toLowerCase() === OWNERSHIP_MARKER : entry === OWNERSHIP_MARKER
}

async function removeLinkEntry(context, path) {
  // Node's non-recursive rm removes a directory junction itself.  The direct
  // junction regression test keeps the target outside this tree intact.
  await limited(context, () => rm(path, { force: false, recursive: false, maxRetries: 3, retryDelay: 50 }))
}

async function limited(context, operation) {
  return context.semaphore.use(async () => {
    context.onOperationStart?.()
    try {
      return await operation()
    } finally {
      context.onOperationEnd?.()
    }
  })
}

class Semaphore {
  constructor(limit) {
    this.limit = limit
    this.active = 0
    this.waiters = []
  }

  async use(operation) {
    await this.acquire()
    try {
      return await operation()
    } finally {
      this.release()
    }
  }

  acquire() {
    if (this.active < this.limit) {
      this.active += 1
      return Promise.resolve()
    }
    return new Promise((resolveWaiter) => this.waiters.push(resolveWaiter))
  }

  release() {
    const waiter = this.waiters.shift()
    if (waiter !== undefined) {
      waiter()
      return
    }
    this.active -= 1
  }
}

function sameFile(left, right) {
  return left.dev === right.dev && left.ino === right.ino && left.size === right.size && left.mtimeMs === right.mtimeMs
}

async function main() {
  const [flag, root] = process.argv.slice(2)
  if (flag !== '--root' || root === undefined || root === '') throw new Error('usage: owned-tree-cleanup.mjs --root <absolute-root>')
  await removeOwnedTreeWithoutFollowingReparsePoints(root)
}

if (process.argv[1] !== undefined && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : String(error)}\n`)
    process.exitCode = 1
  })
}
