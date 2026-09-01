import type { DesktopBridgeLike } from '../desktop-bridge'

// Skills 安装目标(MVP 仅 Claude 与 Codex;GitHub 仓库直装、自动更新与 DSH 目标后置)。
export type SkillTarget = 'claude' | 'codex'

export interface InstalledSkill {
  name: string
  target: SkillTarget
  path: string
  skillMdSha256: string
}

/** 单个目标的安装状态:目录不存在 = 未安装(壳层不误建),skills 恒为空。 */
export interface TargetSkills {
  installed: boolean
  skills: InstalledSkill[]
}

export const SKILL_TARGET_LABELS: Record<SkillTarget, string> = { claude: 'Claude', codex: 'Codex' }
export const SKILL_TARGETS: SkillTarget[] = ['claude', 'codex']

/** 壳层对未安装目标上抛 `skills_target_not_installed: <target>`;这里转译为 installed:false。 */
export function isTargetNotInstalled(cause: unknown): boolean {
  const message = cause instanceof Error ? cause.message : String(cause)
  return message.startsWith('skills_target_not_installed')
}

export async function fetchTargetSkills(bridge: DesktopBridgeLike, target: SkillTarget): Promise<TargetSkills> {
  try {
    const skills = await bridge.requestV2<InstalledSkill[]>('skills.list', undefined, { target })
    return { installed: true, skills }
  } catch (cause: unknown) {
    if (isTargetNotInstalled(cause)) return { installed: false, skills: [] }
    throw cause
  }
}

export async function installSkillsZip(
  bridge: DesktopBridgeLike,
  zipPath: string,
  targets: SkillTarget[],
): Promise<InstalledSkill[]> {
  return bridge.requestV2<InstalledSkill[]>('skills.install.zip', undefined, { zipPath, targets })
}

export async function uninstallSkill(bridge: DesktopBridgeLike, target: SkillTarget, name: string): Promise<void> {
  await bridge.requestV2('skills.uninstall', undefined, { target, name })
}

export async function syncSkill(
  bridge: DesktopBridgeLike,
  srcTarget: SkillTarget,
  dstTarget: SkillTarget,
  name: string,
): Promise<void> {
  await bridge.requestV2('skills.sync', undefined, { srcTarget, dstTarget, name })
}

/** 面板展示用:SKILL.md sha 前 8 位(完整 sha 放 title 提示)。 */
export function shaShort(skill: InstalledSkill): string {
  return skill.skillMdSha256.slice(0, 8)
}

/** 双目标场景下的另一个目标。 */
export function otherTarget(target: SkillTarget): SkillTarget {
  return target === 'claude' ? 'codex' : 'claude'
}
