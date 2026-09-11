/** Copy for the Desktop Model settings section, keyed by {@link DesktopModelLocaleKey}. */

/** Locale keys owned by the Desktop Model settings page. */
export type DesktopModelLocaleKey =
  | 'nav'
  | 'title'
  | 'intro'
  | 'providerLabel'
  | 'modelLabel'
  | 'baseURLLabel'
  | 'keyLabel'
  | 'keyPresent'
  | 'keyMissing'
  | 'test'
  | 'testing'
  | 'testOk'
  | 'testFailed'
  | 'fetchList'
  | 'filled'
  | 'unavailable'
  | 'retry'
  | 'note'

export const en: Record<DesktopModelLocaleKey, string> = {
  nav: 'Model',
  title: 'Model connection',
  intro: 'Check the configured provider, test the connection, and pick the default model from the live list.',
  providerLabel: 'Provider',
  modelLabel: 'Default model',
  baseURLLabel: 'Base URL',
  keyLabel: 'API key',
  keyPresent: 'stored',
  keyMissing: 'missing',
  test: 'Test connection',
  testing: 'Testing…',
  testOk: 'Connection OK',
  testFailed: 'Connection failed',
  fetchList: 'Fetch model list',
  filled: 'Filled into the list above',
  unavailable: 'Model state is unavailable right now.',
  retry: 'Retry',
  note: 'The key never leaves this machine; probes go directly from the app to the configured endpoint.',
}

export const zh: Record<DesktopModelLocaleKey, string> = {
  nav: '模型',
  title: '模型连接',
  intro: '查看当前提供商配置,测试连接,并从线上列表选择默认模型。',
  providerLabel: '提供商',
  modelLabel: '默认模型',
  baseURLLabel: '接口地址',
  keyLabel: 'API 密钥',
  keyPresent: '已保存',
  keyMissing: '未配置',
  test: '测试连接',
  testing: '测试中…',
  testOk: '连接正常',
  testFailed: '连接失败',
  fetchList: '拉取模型列表',
  filled: '已回填到上方列表',
  unavailable: '模型状态暂时不可用。',
  retry: '重试',
  note: '密钥不离开本机;探测请求由应用直连所配置的接口。',
}
