/** Build identity baked into the overlay bundle at packaging time. */

export interface BuildMeta {
  readonly officialVersion: string
  readonly buildVersion: string
}

declare const __DSH_OVERLAY_META__: BuildMeta | undefined

const FALLBACK: BuildMeta = { officialVersion: '0.0.0', buildVersion: '0.0.0_dev' }

/** Build identity of the running overlay; development bundles report `_dev`. */
export function currentBuildMeta(): BuildMeta {
  const baked = typeof __DSH_OVERLAY_META__ === 'undefined' ? undefined : __DSH_OVERLAY_META__
  if (baked !== undefined && typeof baked.officialVersion === 'string' && baked.officialVersion !== '') return baked
  return FALLBACK
}

interface ParsedOfficial {
  readonly core: readonly [number, number, number]
  readonly pre: readonly (string | number)[]
}

/** Parse the official `major.minor.patch[-prerelease]` version used by the baseline lock. */
export function parseOfficialVersion(version: string): ParsedOfficial | null {
  const match = /^(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?$/u.exec(version.trim())
  if (match === null) return null
  const pre = match[4] === undefined
    ? []
    : match[4].split('.').map(part => (/^\d+$/u.test(part) ? Number(part) : part))
  return { core: [Number(match[1]), Number(match[2]), Number(match[3])], pre }
}

function comparePre(a: readonly (string | number)[], b: readonly (string | number)[]): number {
  if (a.length === 0 && b.length === 0) return 0
  if (a.length === 0) return 1
  if (b.length === 0) return -1
  for (let index = 0; index < Math.max(a.length, b.length); index += 1) {
    const left = a[index]
    const right = b[index]
    if (left === undefined) return -1
    if (right === undefined) return 1
    if (left === right) continue
    const leftNumeric = typeof left === 'number'
    const rightNumeric = typeof right === 'number'
    if (leftNumeric && rightNumeric) return left < right ? -1 : 1
    if (leftNumeric) return -1
    if (rightNumeric) return 1
    return left < right ? -1 : 1
  }
  return 0
}

/** Compare official versions with semver precedence; unparsable values rank lowest. */
export function compareOfficialVersions(a: string, b: string): number {
  const left = parseOfficialVersion(a)
  const right = parseOfficialVersion(b)
  if (left === null || right === null) return left === right ? 0 : left === null ? -1 : 1
  for (let index = 0; index < 3; index += 1) {
    if (left.core[index] !== right.core[index]) return left.core[index]! < right.core[index]! ? -1 : 1
  }
  return comparePre(left.pre, right.pre)
}

/** Split `<officialVersion>_<iteration>` into its two parts. */
export function parseBuildVersion(version: string): { readonly officialVersion: string; readonly iteration: number } | null {
  const match = /^(.+?)_(\d+|dev)$/u.exec(version.trim())
  if (match === null) return null
  const iteration = match[2] === 'dev' ? 0 : Number(match[2])
  if (!Number.isSafeInteger(iteration)) return null
  return { officialVersion: match[1]!, iteration }
}

/** Compare build versions: official baseline first, then the local iteration number. */
export function compareBuildVersions(a: string, b: string): number {
  const left = parseBuildVersion(a)
  const right = parseBuildVersion(b)
  if (left === null || right === null) return compareOfficialVersions(a, b)
  const official = compareOfficialVersions(left.officialVersion, right.officialVersion)
  if (official !== 0) return official
  if (left.iteration === right.iteration) return 0
  return left.iteration < right.iteration ? -1 : 1
}
