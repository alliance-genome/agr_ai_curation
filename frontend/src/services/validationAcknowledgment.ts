/** Only an explicit human dialog can acknowledge extraction-only operation. */
export interface ValidationCoverageScope {
  configuration_fingerprint: string
  data_type: string
  unvalidated_fields: Array<{ path: string; label: string }>
  disabled_checks: string[]
  status: 'not_database_validated'
}

type Review = (scopes: ValidationCoverageScope[], signal?: AbortSignal | null) => Promise<boolean>
let review: Review | undefined
export function registerValidationReview(handler: Review): () => void {
  review = handler
  return () => { if (review === handler) review = undefined }
}

export async function fetchWithValidationAcknowledgment(url: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const response = await fetch(url, init)
  if (response.status !== 409 || !review) return response
  const payload = await response.clone().json().catch(() => null)
  if (payload?.detail?.code !== 'validation_acknowledgment_required' || !Array.isArray(payload.detail.scopes)) return response
  const scopes: ValidationCoverageScope[] = payload.detail.scopes
  if (!await review(scopes, init?.signal) || init?.signal?.aborted) {
    throw new DOMException('Canceled: database-validation settings were not changed.', 'AbortError')
  }
  const acknowledgment = await fetch('/api/validation-acknowledgments', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: init?.signal,
    body: JSON.stringify({ acknowledge_extraction_only: true, scopes }),
  })
  if (!acknowledgment.ok) throw new Error('Could not record your extraction-only choice. Nothing was saved or started. Try again.')
  // Retry the identical request once. The server recomputes current coverage;
  // changed configuration/access cannot inherit a stale acknowledgment.
  return fetch(url, init)
}
