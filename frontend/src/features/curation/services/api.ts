export async function readCurationApiError(response: Response): Promise<string> {
  try {
    const payload = await response.json() as { detail?: string; message?: string }
    return payload.detail || payload.message || 'Request failed'
  } catch {
    return 'Request failed'
  }
}
