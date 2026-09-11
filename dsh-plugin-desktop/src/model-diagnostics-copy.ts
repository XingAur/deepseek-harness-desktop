/** Copy for the model diagnostics window, by locale. */

import type { DesktopLocale } from './runtime.ts'

/** One locale's complete model-diagnostics copy. */
export interface ModelDiagnosticsCopy {
  readonly title: string
  readonly provider: string
  readonly providerLabel: string
  readonly modelLabel: string
  readonly baseURLLabel: string
  readonly model: string
  readonly baseURL: string
  readonly apiKey: string
  readonly apiKeyPresent: string
  readonly apiKeyMissing: string
  readonly test: string
  readonly testOk: string
  readonly testFailed: string
  readonly fetchList: string
  readonly fetchFailed: string
  readonly current: string
  readonly setDefault: string
  readonly setDone: string
  readonly empty: string
  readonly close: string
  readonly note: string
}

const EN: ModelDiagnosticsCopy = Object.freeze({
  title: 'Model Diagnostics',
  provider: 'Provider',
  providerLabel: 'Provider',
  modelLabel: 'Default model',
  baseURLLabel: 'Base URL',
  model: 'Default model',
  baseURL: 'Base URL',
  apiKey: 'API key',
  apiKeyPresent: 'stored',
  apiKeyMissing: 'missing',
  test: 'Test connection',
  testOk: 'Connection OK',
  testFailed: 'Connection failed',
  fetchList: 'Fetch model list',
  fetchFailed: 'Fetching models failed',
  current: 'current',
  setDefault: 'Set as default',
  setDone: 'Saved; new sessions use it',
  empty: 'No models returned.',
  close: 'Close',
  note: 'The key never leaves this machine; requests go directly from Desktop to the configured endpoint.',
})

const ZH: ModelDiagnosticsCopy = Object.freeze({
  title: '模型诊断',
  provider: '提供商',
  providerLabel: '提供商',
  modelLabel: '默认模型',
  baseURLLabel: '接口地址',
  model: '默认模型',
  baseURL: '接口地址',
  apiKey: 'API 密钥',
  apiKeyPresent: '已保存',
  apiKeyMissing: '未配置',
  test: '测试连接',
  testOk: '连接正常',
  testFailed: '连接失败',
  fetchList: '拉取模型列表',
  fetchFailed: '拉取模型列表失败',
  current: '当前',
  setDefault: '设为默认',
  setDone: '已保存,新会话生效',
  empty: '未返回任何模型。',
  close: '关闭',
  note: '密钥不离开本机;请求由桌面端直连所配置的接口。',
})

/** Resolve the model-diagnostics copy for one locale. */
export function desktopModelDiagnosticsCopy(locale: DesktopLocale): ModelDiagnosticsCopy {
  return locale === 'zh' ? ZH : EN
}
