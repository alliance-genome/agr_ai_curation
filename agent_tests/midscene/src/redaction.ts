const SECRET_KEY_PATTERN = /(authorization|cookie|api[-_]?key|token|password|secret)/i
const BEARER_PATTERN = /\bBearer\s+[A-Za-z0-9._~+/=-]+/gi
const API_KEY_PATTERN = /\b(sk-[A-Za-z0-9_-]{12,})\b/g
const COOKIE_PATTERN = /\b([A-Za-z0-9_-]+)=([^;\s]{12,})/g

export function redactText(value: string): string {
  return value
    .replace(BEARER_PATTERN, 'Bearer [REDACTED]')
    .replace(API_KEY_PATTERN, '[REDACTED_API_KEY]')
    .replace(COOKIE_PATTERN, '$1=[REDACTED]')
}

export function redactSecrets(value: unknown, seen = new WeakSet<object>()): unknown {
  if (typeof value === 'string') return redactText(value)
  if (value === null || typeof value !== 'object') return value
  if (seen.has(value)) return '[CIRCULAR]'
  seen.add(value)
  if (Array.isArray(value)) return value.map((item) => redactSecrets(item, seen))
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [
      key,
      SECRET_KEY_PATTERN.test(key) ? '[REDACTED]' : redactSecrets(item, seen),
    ]),
  )
}

export function sanitizeHeaders(headers: Headers | Record<string, string>): Record<string, string> {
  const entries = headers instanceof Headers ? [...headers.entries()] : Object.entries(headers)
  return Object.fromEntries(
    entries.map(([key, value]) => [key, SECRET_KEY_PATTERN.test(key) ? '[REDACTED]' : redactText(value)]),
  )
}

export function compactEvidence(value: unknown, maxChars: number): unknown {
  const redacted = redactSecrets(value)
  const serialized = JSON.stringify(redacted) ?? String(redacted)
  if (serialized.length <= maxChars) return redacted
  const compacted = {
    truncated: true,
    original_chars: serialized.length,
    preview: '',
  }
  const availableChars = Math.max(0, maxChars - JSON.stringify(compacted).length)
  compacted.preview = redactText(serialized.slice(0, availableChars))
  while (compacted.preview && JSON.stringify(compacted).length > maxChars) {
    const overflow = JSON.stringify(compacted).length - maxChars
    compacted.preview = compacted.preview.slice(0, Math.max(0, compacted.preview.length - overflow))
  }
  return compacted
}

export function compactDiagnostic(value: unknown, maxChars: number): string {
  const compacted = compactEvidence(value, maxChars)
  return typeof compacted === 'string' ? compacted : JSON.stringify(compacted) ?? String(compacted)
}
