/** Copy for the Desktop model-account settings page, keyed by {@link DesktopAccountsLocaleKey}. */

/** Locale keys owned by the Desktop model-account settings page. */
export type DesktopAccountsLocaleKey =
  | 'nav'
  | 'title'
  | 'intro'
  | 'unavailable'
  | 'loading'
  | 'empty'
  | 'retry'
  | 'notSignedIn'
  | 'activePrefix'
  | 'savedPrefix'
  | 'addAccount'
  | 'adding'
  | 'activateAccount'
  | 'removeAccount'
  | 'logoutAccount'
  | 'inFlight'
  | 'switchedRestart'
  | 'loggedOutRestart'
  | 'removedDone'
  | 'addStarted'
  | 'operationFailed'
  | 'syncTitle'
  | 'syncIntro'
  | 'syncAction'
  | 'syncing'
  | 'syncFailed'
  | 'syncFoundPrefix'
  | 'syncUpToDate'
  | 'syncSkipped'
  | 'syncUsageHint'

/** English copy. */
export const en: Record<DesktopAccountsLocaleKey, string> = {
  nav: 'Accounts',
  title: 'Model accounts',
  intro: 'Sign in with several accounts per provider and switch between them. The active credential signs every model request; a restart is recommended after switching or signing out.',
  unavailable: 'The running composition mounts no model-account capability.',
  loading: 'Loading accounts…',
  empty: 'No manageable model account detected yet.',
  retry: 'Retry',
  notSignedIn: 'Not signed in',
  activePrefix: 'Active',
  savedPrefix: 'Saved accounts',
  addAccount: 'Add account',
  adding: 'Signing in…',
  activateAccount: 'Use',
  removeAccount: 'Remove',
  logoutAccount: 'Sign out',
  inFlight: 'A sign-in is already running for this provider.',
  switchedRestart: 'Account switched. Restart the app to apply it everywhere.',
  loggedOutRestart: 'Signed out. Restart the app to apply it everywhere.',
  removedDone: 'Saved account removed.',
  addStarted: 'Sign-in started. Finish it in the dialog window; this list refreshes automatically.',
  operationFailed: 'The account operation failed. Please try again.',
  syncTitle: 'Sync model catalog',
  syncIntro: 'Fetch each signed-in provider\'s live model listing and add newly released models so they can be selected immediately. Discovered models inherit approximate capacities from the provider\'s widest known model.',
  syncAction: 'Sync now',
  syncing: 'Syncing…',
  syncFailed: 'The model catalog sync failed. Check the network or the provider credential, then retry.',
  syncFoundPrefix: 'new models: ',
  syncUpToDate: 'up to date, nothing new.',
  syncSkipped: 'skipped',
  syncUsageHint: 'Newly discovered models appear in model selection after the Models page reloads. Their context window and pricing are approximate until the built-in catalog catches up.',
}

/** Chinese copy. */
export const zh: Record<DesktopAccountsLocaleKey, string> = {
  nav: '账号',
  title: '模型账号',
  intro: '每个模型服务商可登录多个账号并随时切换。当前生效的账号用于所有模型请求；切换或退出后建议重启应用。',
  unavailable: '当前组合未启用模型账号管理。',
  loading: '正在加载账号…',
  empty: '暂未检测到可管理的模型账号。',
  retry: '重试',
  notSignedIn: '未登录',
  activePrefix: '当前生效',
  savedPrefix: '已保存的账号',
  addAccount: '添加账号',
  adding: '登录中…',
  activateAccount: '启用',
  removeAccount: '移除',
  logoutAccount: '退出登录',
  inFlight: '该账号已有登录流程在进行中。',
  switchedRestart: '账号已切换，重启应用后全局生效。',
  loggedOutRestart: '已退出登录，重启应用后全局生效。',
  removedDone: '已移除保存的账号。',
  addStarted: '已开始登录，请在弹出的窗口中完成；列表会自动刷新。',
  operationFailed: '账号操作失败，请重试。',
  syncTitle: '同步模型目录',
  syncIntro: '用已登录服务商的凭证拉取其线上模型列表，把新发布的模型自动加入本地目录，立即可选可用。新发现的模型会沿用该服务商已知最宽模型的容量近似值。',
  syncAction: '立即同步',
  syncing: '同步中…',
  syncFailed: '模型目录同步失败，请检查网络或服务商凭证后重试。',
  syncFoundPrefix: '新增模型：',
  syncUpToDate: '已是最新，没有新模型。',
  syncSkipped: '已跳过',
  syncUsageHint: '新发现的模型在模型页刷新后即可在模型选择中使用；其上下文窗口和价格为近似值，待内置目录更新后自动精确。',
}
