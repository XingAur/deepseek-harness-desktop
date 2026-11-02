/** Catalog routes whose official login is OAuth-first. Overlay never copies ~/.dsh credentials. */
export const OAUTH_LOGIN_PROVIDERS = ['openai-codex', 'xai'] as const

export type OauthLoginProvider = (typeof OAUTH_LOGIN_PROVIDERS)[number]

export function isOauthLoginProvider(value: string): value is OauthLoginProvider {
  return (OAUTH_LOGIN_PROVIDERS as readonly string[]).includes(value)
}

/** Credential record address official llm-pi-ai registers (`llm-pi-ai/<route>`). */
export function oauthCredentialKey(provider: OauthLoginProvider): string {
  return `llm-pi-ai/${provider}`
}
