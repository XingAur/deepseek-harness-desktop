export const MAX_DIAGNOSTIC_TEXT_BYTES = 4 * 1024

export function redactDiagnostic(value: unknown): string {
  const text = typeof value === 'string' ? value : String(value)
  const redacted = text
    .replace(/-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----/g, '[REDACTED PRIVATE KEY]')
    .replace(/^(authorization|proxy-authorization|x-api-key|api-key|cookie|set-cookie)\s*:\s*.*$/gim, '$1: [REDACTED]')
    .replace(/\b(?:api[_-]?key|oauth[_-]?token|access[_-]?token|refresh[_-]?token|id[_-]?token)\s*[:=]\s*[^\s,;]+/gi, (match) => `${match.split(/[:=]/, 1)[0]}=[REDACTED]`)
    .replace(/\b[A-Z][A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY|COOKIE)[A-Z0-9_]*\s*=\s*[^\s,;]+/g, (match) => `${match.split('=', 1)[0]}=[REDACTED]`)
  return truncateUtf8(redacted, MAX_DIAGNOSTIC_TEXT_BYTES)
}

function truncateUtf8(value: string, maximumBytes: number): string {
  if (Buffer.byteLength(value, 'utf8') <= maximumBytes) return value
  const suffix = '…'
  let result = ''
  for (const character of value) {
    if (Buffer.byteLength(result + character + suffix, 'utf8') > maximumBytes) break
    result += character
  }
  return `${result}${suffix}`
}
