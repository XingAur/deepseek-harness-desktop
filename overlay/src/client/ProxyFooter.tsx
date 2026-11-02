import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import type { Context as ClientContext } from '@deepseek-ai/cordis'
import type { PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import type {} from '@deepseek-ai/dsh-client-ui-settings-models/client'

type ProxyLocaleKey =
  | 'title' | 'intro' | 'mode' | 'modeSystem' | 'modeManual' | 'modeDirect'
  | 'systemHint' | 'manualHint' | 'directHint' | 'url' | 'urlPlaceholder'
  | 'credentialsHint' | 'urlEditedHint' | 'bypass' | 'bypassHint' | 'loopbackHint'
  | 'save' | 'saving' | 'test' | 'testing' | 'testHint' | 'refresh' | 'refreshing'
  | 'loading' | 'saved' | 'refreshed' | 'unsaved' | 'loadFailed' | 'refreshFailed'
  | 'saveFailed' | 'testFailed' | 'testOk' | 'testOkProxy' | 'testOkDirect' | 'testOrigin'
  | 'effectiveTitle' | 'effectiveMode' | 'effectiveProxy' | 'effectiveSystem'
  | 'effectiveUnavailable' | 'appliedBypass' | 'systemTitle' | 'systemReady'
  | 'systemDirect' | 'systemUnsupported' | 'systemError' | 'httpProxy' | 'httpsProxy'
  | 'systemBypass' | 'none' | 'directRoute' | 'stale' | 'configWarning'
  | 'errorNetwork' | 'errorResponse' | 'errorHttp' | 'errorUnauthorized' | 'errorMode'
  | 'errorUrl' | 'errorNoProxy' | 'errorBypass' | 'errorProxyRefused' | 'errorProxyAuth'
  | 'errorDns' | 'errorTimeout' | 'errorTls' | 'errorUnknown' | 'errorConfig'
  | 'errorRead' | 'errorWrite' | 'errorUrlLength' | 'errorScheme' | 'errorPlatform'
  | 'errorSystemRead' | 'errorSystemConfig' | 'errorPac' | 'errorAutoDiscovery'
  | 'errorSocks' | 'errorScoped' | 'errorList' | 'errorBypassUnsupported'
  | 'errorTarget' | 'errorRuntime' | 'errorTransport'

const NAMESPACE = 'overlay.proxy'
const en: Record<ProxyLocaleKey, string> = {
  title: 'Model network',
  intro: 'Applies to all model providers, not MCP or other tools. No provider list is needed.',
  mode: 'Connection mode',
  modeSystem: 'Follow system (default)',
  modeManual: 'Manual proxy',
  modeDirect: 'Direct',
  systemHint: 'Use the detected system HTTP/HTTPS proxies and bypass rules. Unsupported or unreadable settings do not silently fall back to direct.',
  manualHint: 'Use an HTTP or HTTPS proxy for model requests. A failed proxy does not fall back to direct.',
  directHint: 'Connect directly, ignoring system proxies and proxy environment variables.',
  url: 'Manual proxy URL',
  urlPlaceholder: 'http://127.0.0.1:7890',
  credentialsHint: 'Stored credentials are hidden and preserved while this URL is unchanged.',
  urlEditedHint: 'Editing replaces the entire URL. Include credentials again if the proxy requires them; credential-bearing input is masked.',
  bypass: 'Extra direct destinations',
  bypassHint: 'Separate domains or IP addresses with commas or new lines. Matching destinations bypass the proxy.',
  loopbackHint: 'Loopback destinations always connect directly. Successfully detected system bypass rules also apply in manual proxy mode.',
  save: 'Save',
  saving: 'Saving…',
  test: 'Test connection',
  testing: 'Testing…',
  testHint: 'Test the current inputs without saving them. Success only confirms this test connection, not every model provider.',
  refresh: 'Refresh detection',
  refreshing: 'Refreshing…',
  loading: 'Loading proxy settings…',
  saved: 'Saved. New model requests use these settings; in-flight requests keep their original route.',
  refreshed: 'System detection and saved status refreshed. Unsaved inputs were kept.',
  unsaved: 'Unsaved changes. The effective settings below have not changed.',
  loadFailed: 'Could not load proxy settings.',
  refreshFailed: 'Could not refresh proxy detection.',
  saveFailed: 'Could not save proxy settings.',
  testFailed: 'Connection test failed.',
  testOk: 'Connection test succeeded. Nothing was saved.',
  testOkProxy: 'The test connected through a proxy. Nothing was saved.',
  testOkDirect: 'The test connected directly. Nothing was saved.',
  testOrigin: 'Test connection address',
  effectiveTitle: 'Effective settings for new model requests',
  effectiveMode: 'Saved mode',
  effectiveProxy: 'Manual proxy',
  effectiveSystem: 'Use the detected routes below, subject to destination bypass rules.',
  effectiveUnavailable: 'System routing is unavailable. Requests that need it will fail rather than silently connect directly.',
  appliedBypass: 'Saved extra direct destinations',
  systemTitle: 'Detected system settings',
  systemReady: 'System proxy detected',
  systemDirect: 'No system proxy enabled (direct)',
  systemUnsupported: 'Unsupported system proxy configuration',
  systemError: 'System proxy detection failed',
  httpProxy: 'HTTP route',
  httpsProxy: 'HTTPS route',
  systemBypass: 'System bypass rules',
  none: 'None',
  directRoute: 'Direct',
  stale: 'Showing the last successfully read status. Refresh to try again.',
  configWarning: 'Saved configuration needs attention.',
  errorNetwork: 'The request could not reach the application. Check the connection and try again.',
  errorResponse: 'The application returned an invalid response. Refresh or check that the client and host versions match.',
  errorHttp: 'The application rejected the request. Try again or check the host status.',
  errorUnauthorized: 'The request was not authorized. Reload the application and try again.',
  errorMode: 'Choose Follow system, Manual proxy, or Direct.',
  errorUrl: 'Enter a valid HTTP or HTTPS proxy URL.',
  errorNoProxy: 'Manual mode requires a proxy URL.',
  errorBypass: 'One or more direct destinations are invalid. Check the domain or IP address entries.',
  errorProxyRefused: 'The proxy refused the connection. Check that it is running and the address is correct.',
  errorProxyAuth: 'Proxy authentication failed. Check the credentials in the full proxy URL.',
  errorDns: 'The connection address could not be resolved. Check DNS and proxy settings.',
  errorTimeout: 'The request timed out. Check the network and try again.',
  errorTls: 'TLS verification failed. Check the proxy and certificate configuration.',
  errorUnknown: 'The operation failed. Check the proxy settings and try again.',
  errorConfig: 'The proxy configuration is invalid.',
  errorRead: 'The saved proxy configuration could not be read.',
  errorWrite: 'The proxy configuration could not be written. Check the application data directory permissions.',
  errorUrlLength: 'The proxy URL is too long.',
  errorScheme: 'Only HTTP and HTTPS proxy URLs are supported.',
  errorPlatform: 'System proxy detection is not supported on this platform. Choose Manual proxy or Direct.',
  errorSystemRead: 'System proxy settings could not be read. Refresh detection or choose another mode.',
  errorSystemConfig: 'The system proxy settings are invalid. Check the system proxy address and port.',
  errorPac: 'Automatic proxy configuration (PAC) is not supported. Choose Manual proxy or Direct.',
  errorAutoDiscovery: 'Automatic proxy discovery is not supported. Choose Manual proxy or Direct.',
  errorSocks: 'This system SOCKS proxy configuration is not supported. Use an HTTP/HTTPS proxy or Direct.',
  errorScoped: 'Interface-scoped system proxy settings are not supported. Choose Manual proxy or Direct.',
  errorList: 'This system proxy list or failover configuration is not supported. Choose Manual proxy or Direct.',
  errorBypassUnsupported: 'This system bypass rule is not supported. Choose Manual proxy or Direct.',
  errorTarget: 'The connection test destination is invalid. Check the application configuration.',
  errorRuntime: 'Model routing is unavailable. Restart the application and retry; the proxy was not bypassed.',
  errorTransport: 'This transport cannot safely use the selected route. No direct fallback was attempted.',
}
const zh: Record<ProxyLocaleKey, string> = {
  title: '模型网络',
  intro: '适用于所有模型厂商，不影响 MCP 或其他工具，无需维护厂商名单。',
  mode: '连接模式',
  modeSystem: '跟随系统（默认）',
  modeManual: '手动代理',
  modeDirect: '直连',
  systemHint: '使用检测到的系统 HTTP/HTTPS 代理及绕过规则。不支持或无法读取的系统配置不会静默回退为直连。',
  manualHint: '模型请求使用 HTTP 或 HTTPS 代理；代理不可用时不会回退为直连。',
  directHint: '直接连接，忽略系统代理及代理环境变量。',
  url: '手动代理 URL',
  urlPlaceholder: 'http://127.0.0.1:7890',
  credentialsHint: '已保存的凭据不会回显；不修改此地址即可保留凭据。',
  urlEditedHint: '编辑地址会替换完整 URL；如需认证，请重新填写凭据。含凭据的输入会被遮挡。',
  bypass: '额外直连地址',
  bypassHint: '填写域名或 IP 地址，用逗号或换行分隔；匹配的目标将绕过代理。',
  loopbackHint: '回环地址始终直连；手动代理模式也会遵循成功读取的系统绕过规则。',
  save: '保存',
  saving: '保存中…',
  test: '测试连接',
  testing: '测试中…',
  testHint: '按当前输入测试，不会保存。成功仅代表本次连接可达，不代表所有模型厂商均可用。',
  refresh: '刷新检测',
  refreshing: '刷新中…',
  loading: '正在读取代理配置…',
  saved: '已保存，后续模型请求使用新配置；进行中的请求仍使用原路由。',
  refreshed: '系统检测与已保存状态已刷新，未保存的输入已保留。',
  unsaved: '有未保存的修改；下方生效配置尚未改变。',
  loadFailed: '读取代理配置失败。',
  refreshFailed: '刷新代理检测失败。',
  saveFailed: '保存代理配置失败。',
  testFailed: '连接测试失败。',
  testOk: '连接测试成功，未保存任何配置。',
  testOkProxy: '本次测试通过代理连接成功，未保存任何配置。',
  testOkDirect: '本次测试直连成功，未保存任何配置。',
  testOrigin: '本次测试连接地址',
  effectiveTitle: '后续模型请求的生效配置',
  effectiveMode: '已保存模式',
  effectiveProxy: '手动代理',
  effectiveSystem: '按下方检测结果选择路由，同时遵循目标地址的绕过规则。',
  effectiveUnavailable: '系统路由不可用；需要该路由的请求将失败，不会静默直连。',
  appliedBypass: '已保存的额外直连地址',
  systemTitle: '系统代理检测',
  systemReady: '已检测到系统代理',
  systemDirect: '未启用系统代理（直连）',
  systemUnsupported: '系统代理配置暂不支持',
  systemError: '系统代理检测失败',
  httpProxy: 'HTTP 路由',
  httpsProxy: 'HTTPS 路由',
  systemBypass: '系统绕过规则',
  none: '无',
  directRoute: '直连',
  stale: '当前显示上次读取成功的状态，请刷新重试。',
  configWarning: '已保存的配置需要处理。',
  errorNetwork: '无法连接应用服务，请检查连接后重试。',
  errorResponse: '应用返回的数据无效，请刷新或检查前后端版本是否匹配。',
  errorHttp: '应用拒绝了此请求，请重试或检查服务状态。',
  errorUnauthorized: '请求未获授权，请重新加载应用后重试。',
  errorMode: '请选择跟随系统、手动代理或直连。',
  errorUrl: '请输入有效的 HTTP 或 HTTPS 代理 URL。',
  errorNoProxy: '手动模式必须填写代理 URL。',
  errorBypass: '部分直连地址无效，请检查域名或 IP 地址条目。',
  errorProxyRefused: '代理拒绝连接，请检查代理是否启动以及地址是否正确。',
  errorProxyAuth: '代理认证失败，请检查完整代理 URL 中的凭据。',
  errorDns: '无法解析连接地址，请检查 DNS 和代理配置。',
  errorTimeout: '请求超时，请检查网络后重试。',
  errorTls: 'TLS 校验失败，请检查代理与证书配置。',
  errorUnknown: '操作失败，请检查代理配置后重试。',
  errorConfig: '代理配置无效。',
  errorRead: '无法读取已保存的代理配置。',
  errorWrite: '无法写入代理配置，请检查应用数据目录的权限。',
  errorUrlLength: '代理 URL 过长。',
  errorScheme: '仅支持 HTTP 和 HTTPS 代理 URL。',
  errorPlatform: '当前平台不支持系统代理检测，请选择手动代理或直连。',
  errorSystemRead: '无法读取系统代理设置，请刷新检测或选择其他模式。',
  errorSystemConfig: '系统代理设置无效，请检查系统代理地址和端口。',
  errorPac: '暂不支持自动代理配置（PAC），请选择手动代理或直连。',
  errorAutoDiscovery: '暂不支持自动发现代理，请选择手动代理或直连。',
  errorSocks: '暂不支持此系统 SOCKS 代理配置，请使用 HTTP/HTTPS 代理或直连。',
  errorList: '暂不支持此系统代理列表或故障转移配置，请选择手动代理或直连。',
  errorBypassUnsupported: '暂不支持此系统绕过规则，请选择手动代理或直连。',
  errorScoped: '暂不支持按网络接口区分的系统代理配置，请选择手动代理或直连。',
  errorTarget: '连接测试的目标地址无效，请检查应用配置。',
  errorRuntime: '模型网络路由不可用，请重启应用后重试；程序未绕过代理。',
  errorTransport: '此连接方式暂时无法安全使用所选路由，程序未回退直连。',
}

declare module '@deepseek-ai/dsh-client-ui-slots' {
  interface LocaleNamespaceMap {
    'overlay.proxy': ProxyLocaleKey
  }
}

type Translate = (key: ProxyLocaleKey) => string
interface FooterProps extends PropsRuntime<'settings.models.footer'> {
  readonly t: Translate
}

type ProxyMode = 'system' | 'manual' | 'direct'
type SystemStatus = 'ready' | 'direct' | 'unsupported' | 'error'
type Operation = 'load' | 'refresh' | 'save' | 'test'
interface ProxyConfigView {
  readonly mode: ProxyMode
  readonly url: string
  readonly hasCredentials: boolean
  readonly bypass: readonly string[]
  readonly system: {
    readonly status: SystemStatus
    readonly httpProxy: string
    readonly httpsProxy: string
    readonly bypass: readonly string[]
    readonly error?: string
  }
  readonly error?: string
}
interface ProxyDraft {
  readonly mode: ProxyMode
  readonly url: string
  readonly urlEdited: boolean
  readonly bypassText: string
}
interface TestResult {
  readonly route?: 'proxy' | 'direct'
  readonly origin?: string
}
interface PendingRequest {
  readonly kind: Operation
  readonly controller: AbortController
  timeout?: ReturnType<typeof setTimeout>
  timedOut: boolean
}

const MODE_LABELS: Record<ProxyMode, ProxyLocaleKey> = {
  system: 'modeSystem', manual: 'modeManual', direct: 'modeDirect',
}
const MODE_HINTS: Record<ProxyMode, ProxyLocaleKey> = {
  system: 'systemHint', manual: 'manualHint', direct: 'directHint',
}
const SYSTEM_LABELS: Record<SystemStatus, ProxyLocaleKey> = {
  ready: 'systemReady', direct: 'systemDirect', unsupported: 'systemUnsupported', error: 'systemError',
}
const OPERATION_ERRORS: Record<Operation, ProxyLocaleKey> = {
  load: 'loadFailed', refresh: 'refreshFailed', save: 'saveFailed', test: 'testFailed',
}
const ERROR_KEYS: Readonly<Record<string, ProxyLocaleKey>> = {
  'invalid-config': 'errorConfig',
  'invalid-mode': 'errorMode',
  'invalid-url': 'errorUrl',
  'url-too-long': 'errorUrlLength',
  'unsupported-proxy-scheme': 'errorScheme',
  'invalid-bypass': 'errorBypass',
  'manual-url-required': 'errorNoProxy',
  'config-read-failed': 'errorRead',
  'config-write-failed': 'errorWrite',
  'system-proxy-unsupported-platform': 'errorPlatform',
  'system-proxy-read-failed': 'errorSystemRead',
  'system-proxy-invalid-config': 'errorSystemConfig',
  'system-proxy-pac-unsupported': 'errorPac',
  'system-proxy-autodiscovery-unsupported': 'errorAutoDiscovery',
  'system-proxy-socks-unsupported': 'errorSocks',
  'system-proxy-scoped-unsupported': 'errorScoped',
  'system-proxy-list-unsupported': 'errorList',
  'system-proxy-bypass-unsupported': 'errorBypassUnsupported',
  'no-proxy': 'errorNoProxy',
  'proxy-refused': 'errorProxyRefused',
  'proxy-auth': 'errorProxyAuth',
  dns: 'errorDns',
  timeout: 'errorTimeout',
  tls: 'errorTls',
  unknown: 'errorUnknown',
  'request-failed': 'errorUnknown',
  'invalid-target-url': 'errorTarget',
  'model-proxy-disposed': 'errorRuntime',
  'model-proxy-runtime-unavailable': 'errorRuntime',
  'model-proxy-transport-unsupported': 'errorTransport',
}

function errorKey(code: unknown): ProxyLocaleKey {
  // Never render raw host exceptions: they may contain URLs or credentials.
  return typeof code === 'string' && Object.hasOwn(ERROR_KEYS, code) ? ERROR_KEYS[code]! : 'errorUnknown'
}
class ProxyRequestError extends Error {
  constructor(readonly key: ProxyLocaleKey) { super(key) }
}
function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(item => typeof item === 'string')
}
function isMode(value: unknown): value is ProxyMode {
  return value === 'system' || value === 'manual' || value === 'direct'
}
function isSystemStatus(value: unknown): value is SystemStatus {
  return value === 'ready' || value === 'direct' || value === 'unsupported' || value === 'error'
}
function displayOrigin(value: string): string {
  if (value === '') return ''
  try {
    const parsed = new URL(value)
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') return parsed.origin
  } catch { /* Invalid or non-HTTP addresses are never echoed. */ }
  throw new ProxyRequestError('errorResponse')
}
function configView(payload: Record<string, unknown>): ProxyConfigView {
  const system = payload.system
  if (!isMode(payload.mode) || typeof payload.url !== 'string' || typeof payload.hasCredentials !== 'boolean'
    || !isStringArray(payload.bypass) || !isRecord(system) || !isSystemStatus(system.status)
    || typeof system.httpProxy !== 'string' || typeof system.httpsProxy !== 'string'
    || !isStringArray(system.bypass)) throw new ProxyRequestError('errorResponse')
  return {
    mode: payload.mode,
    url: displayOrigin(payload.url),
    hasCredentials: payload.hasCredentials,
    bypass: payload.bypass,
    system: {
      status: system.status,
      httpProxy: displayOrigin(system.httpProxy),
      httpsProxy: displayOrigin(system.httpsProxy),
      bypass: system.bypass,
      ...(typeof system.error === 'string' ? { error: system.error } : {}),
    },
    ...(typeof payload.error === 'string' ? { error: payload.error } : {}),
  }
}
function requestBody(draft: ProxyDraft): { mode: ProxyMode; bypass: string[]; url?: string } {
  const url = draft.url.trim()
  if (draft.mode === 'manual' && url === '') throw new ProxyRequestError('errorNoProxy')
  if (draft.urlEdited && url !== '') {
    let parsed: URL
    try { parsed = new URL(url) } catch { throw new ProxyRequestError('errorUrl') }
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') throw new ProxyRequestError('errorScheme')
  }
  return {
    mode: draft.mode,
    bypass: draft.bypassText.split(/[,\n]/u).map(entry => entry.trim()).filter(Boolean),
    // The GET URL has no userinfo. Sending it unchanged would erase the saved credentials.
    ...(draft.urlEdited ? { url } : {}),
  }
}
async function requestPayload(request: PendingRequest, draft?: ProxyDraft): Promise<Record<string, unknown>> {
  const body = draft === undefined ? undefined : requestBody(draft)
  const response = await fetch(`/api/desktop/proxy/${request.kind === 'test' ? 'test' : 'config'}`, {
    method: body === undefined ? 'GET' : 'POST',
    credentials: 'same-origin',
    redirect: 'error',
    cache: 'no-store',
    signal: request.controller.signal,
    headers: { accept: 'application/json', ...(body === undefined ? {} : { 'content-type': 'application/json' }) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  })
  if (response.status === 401 || response.status === 403) throw new ProxyRequestError('errorUnauthorized')
  let payload: unknown
  try { payload = await response.json() } catch {
    throw new ProxyRequestError(response.ok ? 'errorResponse' : 'errorHttp')
  }
  if (!response.ok) throw new ProxyRequestError(isRecord(payload) && typeof payload.error === 'string'
    ? errorKey(payload.error) : 'errorHttp')
  if (!isRecord(payload) || typeof payload.ok !== 'boolean') throw new ProxyRequestError('errorResponse')
  if (!payload.ok) throw new ProxyRequestError(errorKey(payload.error))
  return payload
}

export function OverlayProxyFooter(props: FooterProps): ReactNode {
  const { t } = props
  const id = useId()
  const [view, setView] = useState<ProxyConfigView>()
  const [mode, setMode] = useState<ProxyMode>('system')
  const [url, setUrl] = useState('')
  const [urlEdited, setUrlEdited] = useState(false)
  const [bypassText, setBypassText] = useState('')
  const [dirty, setDirty] = useState(false)
  const [busy, setBusy] = useState<Operation | undefined>('load')
  const [notice, setNotice] = useState<'saved' | 'refreshed'>()
  const [test, setTest] = useState<TestResult>()
  const [error, setError] = useState<{ operation: Operation; key: ProxyLocaleKey }>()
  const mounted = useRef(false)
  const draftDirty = useRef(false)
  const pending = useRef<PendingRequest | null>(null)

  const run = useCallback(async (kind: Operation, draft?: ProxyDraft): Promise<void> => {
    // A ref also closes the gap before React renders the disabled buttons.
    if (!mounted.current || pending.current !== null) return
    const request: PendingRequest = { kind, controller: new AbortController(), timedOut: false }
    pending.current = request
    const isCurrent = (): boolean => mounted.current && pending.current === request
    request.timeout = setTimeout(() => {
      request.timedOut = true
      request.controller.abort()
    }, 20_000)
    setBusy(kind)
    setError(undefined)
    setNotice(undefined)
    setTest(undefined)
    try {
      const payload = await requestPayload(request, draft)
      if (!isCurrent()) return
      if (request.timedOut) throw new ProxyRequestError('errorTimeout')
      if (kind === 'test') {
        if ((payload.route !== undefined && payload.route !== 'proxy' && payload.route !== 'direct')
          || (payload.origin !== undefined && typeof payload.origin !== 'string')) {
          throw new ProxyRequestError('errorResponse')
        }
        setTest({
          ...(payload.route === 'proxy' || payload.route === 'direct' ? { route: payload.route } : {}),
          ...(typeof payload.origin === 'string' ? { origin: displayOrigin(payload.origin) } : {}),
        })
      } else {
        const next = configView(payload)
        setView(next)
        // Refresh updates effective/system status without replacing an unsaved draft.
        if (kind !== 'refresh' || !draftDirty.current) {
          setMode(next.mode)
          setUrl(next.url)
          setUrlEdited(false)
          setBypassText(next.bypass.join('\n'))
          draftDirty.current = false
          setDirty(false)
        }
        if (kind === 'save') setNotice('saved')
        if (kind === 'refresh') setNotice('refreshed')
      }
    } catch (cause) {
      if (isCurrent()) setError({ operation: kind, key: request.timedOut ? 'errorTimeout'
        : cause instanceof ProxyRequestError ? cause.key : 'errorNetwork' })
    } finally {
      clearTimeout(request.timeout)
      if (isCurrent()) {
        pending.current = null
        setBusy(undefined)
      }
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    void run('load')
    return () => {
      mounted.current = false
      const request = pending.current
      pending.current = null
      if (request !== null) {
        clearTimeout(request.timeout)
        request.controller.abort()
      }
    }
  }, [run])

  const changed = (): void => {
    draftDirty.current = true
    setDirty(true)
    setNotice(undefined)
    setError(undefined)
    setTest(undefined)
    // Editing during a test invalidates that result even if a mock/transport ignores abort.
    const request = pending.current
    if (request?.kind === 'test') {
      pending.current = null
      clearTimeout(request.timeout)
      request.controller.abort()
      setBusy(undefined)
    }
  }
  const inputDisabled = view === undefined || busy === 'load' || busy === 'save'
  const draft: ProxyDraft = { mode, url, urlEdited, bypassText }
  const systemUnavailable = view?.system.status === 'unsupported' || view?.system.status === 'error'
  const testMessage: ProxyLocaleKey = test?.route === 'proxy' ? 'testOkProxy'
    : test?.route === 'direct' ? 'testOkDirect' : 'testOk'

  return <section className="dshOverlayProxyFooter" aria-labelledby={`${id}-title`} aria-busy={busy !== undefined}>
    <h3 className="dshOverlayTitle" id={`${id}-title`}>{t('title')}</h3>
    <p className="dshOverlayIntro">{t('intro')}</p>
    <fieldset className="dshOverlayProxyModes" disabled={inputDisabled} aria-describedby={`${id}-mode-hint`}>
      <legend>{t('mode')}</legend>
      <div className="dshOverlayRow">
        {(['system', 'manual', 'direct'] as const).map(value => <label key={value} className="dshOverlayCheck">
          <input type="radio" name={`${id}-mode`} value={value} checked={mode === value}
            onChange={() => { changed(); setMode(value) }} />
          {t(MODE_LABELS[value])}
        </label>)}
      </div>
    </fieldset>
    <p className="dshOverlayHint" id={`${id}-mode-hint`}>{t(MODE_HINTS[mode])}</p>
    {mode === 'manual' ? <div className="dshOverlayProxyField">
      <label htmlFor={`${id}-url`}>{t('url')}</label>
      <input id={`${id}-url`} className="dshOverlayInput" value={url}
        type={urlEdited && /\/\/[^/?#]*@/u.test(url) ? 'password' : 'url'}
        autoComplete="off" autoCapitalize="none" spellCheck={false} placeholder={t('urlPlaceholder')}
        disabled={inputDisabled} aria-describedby={`${id}-url-hint`}
        onChange={event => { changed(); setUrlEdited(true); setUrl(event.target.value) }} />
      <p className="dshOverlayHint" id={`${id}-url-hint`}>
        {view?.hasCredentials && !urlEdited ? `${t('credentialsHint')} ` : ''}{t('urlEditedHint')}
      </p>
    </div> : null}
    <div className="dshOverlayProxyField">
      <label htmlFor={`${id}-bypass`}>{t('bypass')}</label>
      <textarea id={`${id}-bypass`} className="dshOverlayTextarea" rows={3} value={bypassText}
        autoComplete="off" autoCapitalize="none" spellCheck={false} disabled={inputDisabled}
        aria-describedby={`${id}-bypass-hint`}
        onChange={event => { changed(); setBypassText(event.target.value) }} />
      <p className="dshOverlayHint" id={`${id}-bypass-hint`}>{t('bypassHint')} {t('loopbackHint')}</p>
    </div>
    {dirty ? <p className="dshOverlayHint" role="status">{t('unsaved')}</p> : null}
    <div className="dshOverlayRow">
      <button type="button" className="dshOverlayBtnSecondary" disabled={busy !== undefined}
        onClick={() => { void run('refresh') }}>
        {busy === 'refresh' ? t('refreshing') : t('refresh')}
      </button>
      <button type="button" className="dshOverlayBtnSecondary" disabled={busy !== undefined || view === undefined}
        aria-describedby={`${id}-test-hint`} onClick={() => { void run('test', draft) }}>
        {busy === 'test' ? t('testing') : t('test')}
      </button>
      <button type="button" className="dshOverlayBtnPrimary" disabled={busy !== undefined || view === undefined}
        onClick={() => { void run('save', draft) }}>
        {busy === 'save' ? t('saving') : t('save')}
      </button>
    </div>
    <p className="dshOverlayHint" id={`${id}-test-hint`}>{t('testHint')}</p>
    {busy === 'load' ? <p className="dshOverlayHint" role="status">{t('loading')}</p> : null}
    {notice !== undefined ? <p className="dshOverlaySaved" role="status">{t(notice)}</p> : null}
    {test !== undefined ? <p className="dshOverlaySaved" role="status">
      {t(testMessage)}{test.origin ? <> {t('testOrigin')}: <code>{test.origin}</code></> : null}
    </p> : null}
    {error !== undefined ? <p className="dshOverlayAlert" role="alert">
      {t(OPERATION_ERRORS[error.operation])} {t(error.key)}
    </p> : null}
    {view !== undefined ? <div className="dshOverlayProxyStatus">
      <h4>{t('effectiveTitle')}</h4>
      {error?.operation === 'refresh' || error?.operation === 'load'
        ? <p className="dshOverlayHint">{t('stale')}</p> : null}
      {view.error ? <p className="dshOverlayAlert" role="alert">{t('configWarning')} {t(errorKey(view.error))}</p> : null}
      <dl>
        <dt>{t('effectiveMode')}</dt><dd>{t(MODE_LABELS[view.mode])}</dd>
        {view.mode === 'manual' ? <>
          <dt>{t('effectiveProxy')}</dt><dd><code>{view.url || t('none')}</code></dd>
        </> : null}
        <dt>{t('appliedBypass')}</dt><dd>{view.bypass.join(', ') || t('none')}</dd>
      </dl>
      {view.mode === 'direct' ? <p className="dshOverlayHint">{t('directHint')}</p> : null}
      {view.mode === 'system' ? <p className={systemUnavailable ? 'dshOverlayAlert' : 'dshOverlayHint'}>
        {t(systemUnavailable ? 'effectiveUnavailable' : 'effectiveSystem')}
      </p> : null}
      <h4>{t('systemTitle')}</h4>
      <p className={systemUnavailable ? 'dshOverlayAlert' : 'dshOverlayHint'}>
        {t(SYSTEM_LABELS[view.system.status])}
        {view.system.error ? ` · ${t(errorKey(view.system.error))}` : ''}
      </p>
      {!systemUnavailable ? <dl>
        <dt>{t('httpProxy')}</dt><dd><code>{view.system.httpProxy || t('directRoute')}</code></dd>
        <dt>{t('httpsProxy')}</dt><dd><code>{view.system.httpsProxy || t('directRoute')}</code></dd>
        <dt>{t('systemBypass')}</dt><dd>{view.system.bypass.join(', ') || t('none')}</dd>
      </dl> : null}
    </div> : null}
  </section>
}

export function applyOverlayProxyFooter(ctx: ClientContext): void {
  ctx.effect(() => ctx.locale.register(NAMESPACE, { zh, en }), 'dsh-overlay-custom: proxy locale')
  ctx.effect(() => ctx.slots.inject('settings.models.footer', () => ctx.slots.register({
    name: 'settings.models.footer',
    id: 'overlay-model-proxy',
    inject: () => ({ t: ctx.locale.bind(NAMESPACE) }) as never,
  }, OverlayProxyFooter as never)), 'dsh-overlay-custom: proxy footer')
}
