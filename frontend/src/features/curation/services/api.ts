export async function readCurationApiError(response: Response): Promise<string> {
  try {
    const payload = await response.json() as unknown
    if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
      return 'Request failed'
    }

    const { detail, message } = payload as Record<string, unknown>
    if (typeof detail === 'string' && detail) {
      return detail
    }
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      const findings = (detail as Record<string, unknown>).findings
      if (Array.isArray(findings)) {
        const finding = findings.find((item) => (
          item
          && typeof item === 'object'
          && !Array.isArray(item)
          && (item as Record<string, unknown>).severity === 'error'
          && typeof (item as Record<string, unknown>).message === 'string'
        )) ?? findings.find((item) => (
          item
          && typeof item === 'object'
          && !Array.isArray(item)
          && typeof (item as Record<string, unknown>).message === 'string'
        ))
        if (finding && typeof finding === 'object' && !Array.isArray(finding)) {
          const findingMessage = (finding as Record<string, unknown>).message
          if (typeof findingMessage === 'string' && findingMessage) {
            return findingMessage
          }
        }
      }
    }
    return typeof message === 'string' && message ? message : 'Request failed'
  } catch {
    return 'Request failed'
  }
}

/**
 * A refusal's machine code (`detail.error`), or null when it has none. Lets one screen
 * handle one refusal by its code with its own signed-off text; the server's message
 * is never shown this way (KANBAN-2159 tracks codes to messages across the screens).
 */
export async function readCurationApiErrorCode(response: Response): Promise<string | null> {
  let payload: unknown
  try {
    payload = await response.json()
  } catch {
    // A body that is not JSON carries no code.
    return null
  }
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    return null
  }
  const { detail } = payload as Record<string, unknown>
  if (!detail || typeof detail !== 'object' || Array.isArray(detail)) {
    return null
  }
  const code = (detail as Record<string, unknown>).error
  return typeof code === 'string' && code ? code : null
}
