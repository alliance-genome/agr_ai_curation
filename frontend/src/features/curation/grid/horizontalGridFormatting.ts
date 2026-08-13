export function formatHorizontalGridValue(value: unknown): string | null {
  if (value === null || value === undefined || value === '') {
    return null
  }

  if (typeof value === 'string') {
    return value
  }

  if (typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }

  if (Array.isArray(value)) {
    return value.map((item) => formatHorizontalGridValue(item) ?? '—').join(', ')
  }

  return JSON.stringify(value)
}
