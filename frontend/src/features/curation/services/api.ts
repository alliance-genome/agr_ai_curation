export const RESULT_QUOTES_REMOVED_FILE_ERROR_CODE = 'result_quotes_removed_file' as const

function extractCurationApiErrorFromPayload(payload: unknown): string {
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
}

function extractCurationApiErrorCodeFromPayload(payload: unknown): string | null {
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

export async function readCurationApiError(response: Response): Promise<string> {
  try {
    return extractCurationApiErrorFromPayload(await response.json() as unknown)
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
  try {
    return extractCurationApiErrorCodeFromPayload(await response.json() as unknown)
  } catch {
    // A body that is not JSON carries no code.
    return null
  }
}

/** Read both the error message and optional structured `detail.error` code in a single JSON parse. */
export async function readCurationApiErrorWithCode(
  response: Response,
): Promise<{ message: string; code: string | null }> {
  try {
    const payload = await response.json() as unknown
    return {
      message: extractCurationApiErrorFromPayload(payload),
      code: extractCurationApiErrorCodeFromPayload(payload),
    }
  } catch {
    return { message: 'Request failed', code: null }
  }
}
