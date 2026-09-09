/** Copy for the Desktop Skills settings page, keyed by {@link DesktopSkillsLocaleKey}. */

/** Locale keys owned by the Desktop Skills settings page. */
export type DesktopSkillsLocaleKey =
  | 'nav'
  | 'title'
  | 'intro'
  | 'catalogTitle'
  | 'catalogIntro'
  | 'loading'
  | 'unavailable'
  | 'retry'
  | 'registryUnavailable'
  | 'empty'
  | 'badgeUserInvocable'
  | 'badgeModelInvocable'
  | 'whenToUseLabel'
  | 'sourceUserAgents'
  | 'sourceUserDsh'
  | 'sourceProjectAgents'
  | 'sourceProjectDsh'
  | 'sourceBundled'
  | 'sourceRuntime'
  | 'sourceCustom'

/** English copy. */
export const en: Record<DesktopSkillsLocaleKey, string> = {
  nav: 'Skills',
  title: 'Skills',
  intro: 'Skill packages discovered by the running Desktop composition.',
  catalogTitle: 'Installed skills',
  catalogIntro: 'Private skills live only on this machine and never enter the repository; shared skills travel with the project to every machine. Skill bodies stay on disk; this page never edits them.',
  loading: 'Loading skills…',
  unavailable: 'The skill catalog could not be read. Please try again.',
  retry: 'Retry',
  registryUnavailable: 'The running composition mounts no skill registry.',
  empty: 'No skills discovered yet. Add skill folders to your user skills directory.',
  badgeUserInvocable: '/command',
  badgeModelInvocable: 'model',
  whenToUseLabel: 'Use when',
  sourceUserAgents: 'Private · this machine',
  sourceUserDsh: 'Private · this machine',
  sourceProjectAgents: 'Shared · repository',
  sourceProjectDsh: 'Shared · repository',
  sourceBundled: 'Built-in',
  sourceRuntime: 'Runtime',
  sourceCustom: 'Private · custom path',
}

/** Simplified Chinese copy. */
export const zh: Record<DesktopSkillsLocaleKey, string> = {
  nav: '技能',
  title: '技能',
  intro: '当前桌面组合发现的技能包。',
  catalogTitle: '已发现的技能',
  catalogIntro: '「本机私有」技能只存在于这台电脑、永不进入仓库；「仓库公用」技能随项目同步到每台机器。技能文件始终保留在磁盘上，此页面不会修改它们。',
  loading: '正在加载技能…',
  unavailable: '技能目录读取失败，请重试。',
  retry: '重试',
  registryUnavailable: '当前组合未挂载技能注册表。',
  empty: '尚未发现技能。请向用户技能目录添加技能文件夹。',
  badgeUserInvocable: '/命令',
  badgeModelInvocable: '模型',
  whenToUseLabel: '适用场景',
  sourceUserAgents: '本机私有',
  sourceUserDsh: '本机私有',
  sourceProjectAgents: '仓库公用',
  sourceProjectDsh: '仓库公用',
  sourceBundled: '内置',
  sourceRuntime: '运行时',
  sourceCustom: '私有 · 自定义路径',
}
