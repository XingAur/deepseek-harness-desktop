/** Copy for the Desktop proxy settings page. */

export type DesktopProxyLocaleKey =
  | 'nav'
  | 'title'
  | 'intro'
  | 'operationFailed'
  | 'restartRequired'
  | 'restarting'
  | 'modelProxyTitle'
  | 'modelProxyIntro'
  | 'modelProxyXai'
  | 'modelProxyCodex'
  | 'modelProxyAnthropic'
  | 'modelProxyGemini'
  | 'modelProxyOwnUrl'
  | 'modelProxyExtraHosts'
  | 'modelProxyExtraHostsHint'
  | 'modelProxyExtraHostsInvalid'
  | 'modelProxyAddName'
  | 'modelProxyAddHost'
  | 'modelProxyAdd'
  | 'modelProxyRemove'
  | 'modelProxyDetect'
  | 'modelProxyDetecting'
  | 'modelProxyDetected'
  | 'modelProxyDetectMissing'
  | 'modelProxySave'
  | 'modelProxyTest'
  | 'modelProxyTesting'
  | 'modelProxyTestOk'
  | 'modelProxyTestInvalidUrl'
  | 'modelProxyTestNoProxy'
  | 'modelProxyTestProxyRefused'
  | 'modelProxyTestProxyAuth'
  | 'modelProxyTestTimeout'
  | 'modelProxyTestTls'
  | 'modelProxyTestDns'
  | 'modelProxyTestTargetUnreachable'
  | 'modelProxyTestUnknown'
  | 'modelProxyStatusEnv'
  | 'modelProxyStatusCustom'
  | 'modelProxyStatusGlobal'
  | 'proxyAdvanced'
  | 'proxyTitle'
  | 'proxyIntro'
  | 'proxyUrl'
  | 'proxyUrlPlaceholder'
  | 'proxySave'
  | 'proxyDetect'
  | 'proxyDetecting'
  | 'proxyDetected'
  | 'proxyDetectMissing'
  | 'proxyInvalid'

export const zh: Record<DesktopProxyLocaleKey, string> = {
  nav: '代理',
  title: '代理',
  intro: '出国模型走梯子，国内请求直连。以后还会在这里加更多代理选项。',
  operationFailed: '暂时无法完成这项操作，请稍后重试。',
  restartRequired: '选择已保存，重启 DSH Desktop 后生效。',
  restarting: '正在重启 DSH Desktop…',
  modelProxyTitle: '模型代理',
  modelProxyIntro: '按厂商勾选谁走代理。默认都用上面同一条。某个厂商要换线，填单独代理。智谱 GLM 不要勾。',
  modelProxyXai: 'xAI / Grok',
  modelProxyCodex: 'OpenAI / Codex',
  modelProxyAnthropic: 'Anthropic',
  modelProxyGemini: 'Google Gemini',
  modelProxyOwnUrl: '单独代理（可选，留空=用共用代理）',
  modelProxyExtraHosts: '自定义模型',
  modelProxyExtraHostsHint: '和 Grok / Codex 一样走模型代理。填名称和 API 域名，不要填智谱、云效。',
  modelProxyExtraHostsInvalid: '名称或域名无效。名称不能空，域名例如 api.anthropic.com。',
  modelProxyAddName: '模型名称',
  modelProxyAddHost: 'API 域名',
  modelProxyAdd: '添加模型',
  modelProxyRemove: '删除',
  modelProxyDetect: '检测本机代理',
  modelProxyDetecting: '正在检测…',
  modelProxyDetected: '已填入本机代理，请保存后重启。',
  modelProxyDetectMissing: '未发现本机 HTTP 代理。可留空沿用 .env，或手动填写。',
  modelProxySave: '保存',
  modelProxyTest: '测试链接',
  modelProxyTesting: '正在测试…',
  modelProxyTestOk: '已通到 api.x.ai，可以保存。智谱和云效不会走这条线。',
  modelProxyTestInvalidUrl: '代理 URL 无效。请使用 http://、https:// 或 socks5://，并带上账号密码。',
  modelProxyTestNoProxy: '当前没有可测的代理（URL 为空且没有 .env 的 HTTP_PROXY）。Grok 会直连，国内网络通常不通。',
  modelProxyTestProxyRefused: '连不上这台代理（端口没开或地址错了）。',
  modelProxyTestProxyAuth: '代理要账号密码，当前用户名或密码不对。',
  modelProxyTestTimeout: '8 秒内没通到 api.x.ai。住宅节点到不了 Grok，不要保存。清空 URL 可退回 Clash。',
  modelProxyTestTls: '代理连上了，但 TLS/证书失败，这条线到不了 Grok。',
  modelProxyTestDns: '代理主机名解析失败，请检查地址。',
  modelProxyTestTargetUnreachable: '代理自己通了，但到不了 api.x.ai。换节点或清空 URL 用 Clash。',
  modelProxyTestUnknown: '测试失败，原因不明。不要保存这条住宅线，先清空 URL。',
  modelProxyStatusEnv: '当前：勾选的模型走 .env 里的 HTTP_PROXY，智谱和云效直连。',
  modelProxyStatusCustom: '当前：勾选的模型走上面填写的代理，智谱和云效直连。保存并重启后生效。',
  modelProxyStatusGlobal: '当前：下面「全部流量」已填写，国内国外都会走梯子，上面的勾选无效。请把它留空。',
  proxyAdvanced: '高级：全部流量出站代理（一般不要打开）',
  proxyTitle: '出站代理（全部流量）',
  proxyIntro: '会让所有请求（含国内）走这条代理，一般不要填。请用上面的模型代理。留空则不影响。',
  proxyUrl: '代理 URL',
  proxyUrlPlaceholder: 'http://127.0.0.1:7890 或 socks5://user:pass@host:1080',
  proxySave: '保存',
  proxyDetect: '检测本机代理',
  proxyDetecting: '正在检测…',
  proxyDetected: '已填入本机代理，请保存后重启。',
  proxyDetectMissing: '未发现本机 HTTP 代理。可留空，或手动填写 http://127.0.0.1:端口。',
  proxyInvalid: '代理 URL 无效。请使用 http://、https:// 或 socks5://。',
}

export const en: Record<DesktopProxyLocaleKey, string> = {
  nav: 'Proxy',
  title: 'Proxy',
  intro: 'Send overseas models through a tunnel and keep domestic requests direct. More proxy options will land here later.',
  operationFailed: 'That operation could not be completed. Try again in a moment.',
  restartRequired: 'Your choice was saved and will take effect after DSH Desktop restarts.',
  restarting: 'Restarting DSH Desktop…',
  modelProxyTitle: 'Model proxy',
  modelProxyIntro: 'Choose which vendors use the tunnel. Checked vendors share the URL above. Set a dedicated URL only when one vendor needs a different proxy. Do not check Zhipu/GLM.',
  modelProxyXai: 'xAI / Grok',
  modelProxyCodex: 'OpenAI / Codex',
  modelProxyAnthropic: 'Anthropic',
  modelProxyGemini: 'Google Gemini',
  modelProxyOwnUrl: 'Dedicated proxy (optional; empty uses the shared URL)',
  modelProxyExtraHosts: 'Custom models',
  modelProxyExtraHostsHint: 'These use the same model proxy as Grok and Codex. Add a name and API host. Do not add Zhipu or Yunxiao.',
  modelProxyExtraHostsInvalid: 'Name or host is invalid. Name cannot be empty; host should look like api.anthropic.com.',
  modelProxyAddName: 'Model name',
  modelProxyAddHost: 'API host',
  modelProxyAdd: 'Add model',
  modelProxyRemove: 'Remove',
  modelProxyDetect: 'Detect local proxy',
  modelProxyDetecting: 'Detecting…',
  modelProxyDetected: 'Filled in a local proxy. Save and restart to apply it.',
  modelProxyDetectMissing: 'No local HTTP proxy found. Leave empty to use .env, or type a URL.',
  modelProxySave: 'Save',
  modelProxyTest: 'Test connection',
  modelProxyTesting: 'Testing…',
  modelProxyTestOk: 'Reached api.x.ai. Safe to save. Zhipu and Yunxiao will not use this proxy.',
  modelProxyTestInvalidUrl: 'That proxy URL is invalid. Use http://, https://, or socks5://, including credentials.',
  modelProxyTestNoProxy: 'No proxy to test (empty URL and no HTTP_PROXY in .env). Grok would connect directly and usually fail.',
  modelProxyTestProxyRefused: 'Could not connect to this proxy (wrong host or the port is closed).',
  modelProxyTestProxyAuth: 'The proxy asked for credentials. The username or password is wrong.',
  modelProxyTestTimeout: 'Could not reach api.x.ai within 8 seconds. Do not save this residential proxy. Clear the URL to fall back to Clash.',
  modelProxyTestTls: 'The proxy connected, but TLS failed. This path cannot reach Grok.',
  modelProxyTestDns: 'The proxy hostname could not be resolved. Check the address.',
  modelProxyTestTargetUnreachable: 'The proxy itself is up, but it cannot reach api.x.ai. Switch nodes or clear the URL.',
  modelProxyTestUnknown: 'The probe failed for an unknown reason. Do not save this residential URL; clear it first.',
  modelProxyStatusEnv: 'Now: checked models use HTTP_PROXY from .env. Zhipu and Yunxiao stay direct.',
  modelProxyStatusCustom: 'Now: checked models use the URL above. Zhipu and Yunxiao stay direct. Save and restart to apply.',
  modelProxyStatusGlobal: 'Now: the all-traffic proxy is set, so domestic and overseas requests both use it. Clear that field.',
  proxyAdvanced: 'Advanced: outbound proxy for all traffic (leave this closed)',
  proxyTitle: 'Outbound proxy (all traffic)',
  proxyIntro: 'Sends every request, including domestic APIs, through this proxy. Prefer the model proxy above. Leave empty to leave other traffic alone.',
  proxyUrl: 'Proxy URL',
  proxyUrlPlaceholder: 'http://127.0.0.1:7890 or socks5://user:pass@host:1080',
  proxySave: 'Save',
  proxyDetect: 'Detect local proxy',
  proxyDetecting: 'Detecting…',
  proxyDetected: 'Filled in a local proxy. Save and restart to apply it.',
  proxyDetectMissing: 'No local HTTP proxy found. Leave empty, or type http://127.0.0.1:<port>.',
  proxyInvalid: 'That proxy URL is invalid. Use http://, https://, or socks5://.',
}
