interface SessionLikeEvent {
  session_id?: unknown
  sessionId?: unknown
}

function normalizeOptionalSessionValue(value: unknown): string | null {
  if (typeof value !== 'string') {
    return null
  }

  const normalizedValue = value.trim()
  return normalizedValue.length > 0 ? normalizedValue : null
}

export function getStreamEventSessionId(event: SessionLikeEvent): string | null {
  return normalizeOptionalSessionValue(event.session_id)
    ?? normalizeOptionalSessionValue(event.sessionId)
}
