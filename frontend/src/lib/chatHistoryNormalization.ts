export function normalizeChatHistoryValue(value: string | null | undefined): string | null {
  if (value == null) {
    return null
  }

  const normalizedValue = value.trim()
  return normalizedValue.length > 0 ? normalizedValue : null
}
