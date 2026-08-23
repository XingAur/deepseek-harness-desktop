export const MAX_DIAGNOSTIC_TEXT_BYTES = 4 * 1024

export function redactDiagnostic(value: unknown): string {
  const text = typeof value === 'string' ? value : String(value)
  const sensitive = 'authorization|proxy-authorization|x-api-key|api-key|api[_-]?key|oauth[_-]?token|access[_-]?token|refresh[_-]?token|id[_-]?token|client[_-]?secret|token|secret|password|passwd|private[_-]?key|cookie|set-cookie'
  const redacted = text
    .replace(/-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----/g, '[REDACTED PRIVATE KEY]')
    .replace(new RegExp(`(["']?)(?:${sensitive})\\1\\s*:\\s*(?:"(?:\\\\.|[^"\\\\])*"|'(?:\\\\.|[^'\\\\])*'|[^,}\\]\r\\n]+)`, 'gi'), (match) => `${match.slice(0, match.indexOf(':') + 1)}"[REDACTED]"`)
    .replace(new RegExp(`(^|[\\r\\n])\\s*(${sensitive})\\s*[:=]\\s*[^\\r\\n]*(?:\\r?\\n[ \\t]+[^\\r\\n]*)*`, 'gim'), '$1$2: [REDACTED]')
    .replace(new RegExp(`\\b(${sensitive})\\s*[:=]\\s*(?:"(?:\\\\.|[^"\\\\])*"|'(?:\\\\.|[^'\\\\])*'|[^\\s,;\\]}]+)`, 'gi'), '$1=[REDACTED]')
    .replace(/\b[A-Z][A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY|COOKIE)[A-Z0-9_]*\s*=\s*(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\s,;\]}]+)/g, (match) => `${match.split('=', 1)[0]}=[REDACTED]`)
    .replace(new RegExp(`([?&])(${sensitive})=([^&#\\s]+)`, 'gi'), '$1$2=[REDACTED]')
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
