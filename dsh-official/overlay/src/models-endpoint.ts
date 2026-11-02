/** Strip the Anthropic-compat suffix so `/models` hits the platform endpoint. */
export function modelsEndpoint(baseURL: string): string {
  const trimmed = baseURL.replace(/\/+$/u, '')
  const root = trimmed.endsWith('/anthropic') ? trimmed.slice(0, -'/anthropic'.length) : trimmed
  return `${root}/models`
}
