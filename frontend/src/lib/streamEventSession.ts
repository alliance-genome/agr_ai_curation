import { normalizeOptionalText } from './normalizeOptionalText'

interface SessionLikeEvent {
  session_id?: unknown
  sessionId?: unknown
}

export function getStreamEventSessionId(event: SessionLikeEvent): string | null {
  return normalizeOptionalText(event.session_id)
    ?? normalizeOptionalText(event.sessionId)
}
