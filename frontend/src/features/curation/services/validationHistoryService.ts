import { readCurationApiError } from './api'

export interface HistoryReference { id: string; sha256: string }
export interface HistoryChoice {
  target_id: string
  decision_unit_id: string
  expected_version: number
  logical_attempt_id: string
}
export interface HistoryEntry extends HistoryReference {
  kind: string
  sequence: number
  created_at: string
  actor: { type: string; id: string } | null
  reason: string | null
  state: string | null
  choices: HistoryChoice[]
  decisions: { action: string; reason: string }[]
}
export interface HistoryPage {
  entries: HistoryEntry[]
  next_after: number | null
  baseline: HistoryReference
  episode_id: string | null
}
export interface HistoryDetail {
  content: string
  next_offset: number | null
  total_bytes: number
}
async function read<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch(path, { credentials: 'include', signal })
  if (!response.ok) throw new Error(await readCurationApiError(response))
  return response.json() as Promise<T>
}
export function fetchValidationHistory(candidateId: string, after = 0, signal?: AbortSignal) {
  return read<HistoryPage>(`/api/curation-workspace/candidates/${encodeURIComponent(candidateId)}/validation-history?after=${after}`, signal)
}
export function fetchValidationHistoryDetail(candidateId: string, ref: HistoryReference, offset = 0, signal?: AbortSignal) {
  const query = new URLSearchParams({ sha256: ref.sha256, offset: String(offset) })
  return read<HistoryDetail>(`/api/curation-workspace/candidates/${encodeURIComponent(candidateId)}/validation-history/${encodeURIComponent(ref.id)}?${query}`, signal)
}


export interface ReviewOperation { operation_id: string; session_id: string; state: string; artifact_count: number; outcome: unknown }
async function command<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`/api/curation-workspace/${path}`, {
    method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  if (!response.ok) throw new Error(await readCurationApiError(response))
  return response.json() as Promise<T>
}
export function requestReview(sessionId: string, candidateIds: string[], guidance: string | null, requestId: string) {
  return command<ReviewOperation>(`sessions/${encodeURIComponent(sessionId)}/review-operations`, {
    request_id: requestId, candidate_ids: candidateIds, guidance,
  })
}
export function fetchSessionReviewOperation(sessionId: string, signal?: AbortSignal) {
  return read<ReviewOperation | null>(`/api/curation-workspace/sessions/${encodeURIComponent(sessionId)}/review-operation`, signal)
}
export function fetchCandidateReviewOperation(candidateId: string, signal?: AbortSignal) {
  return read<ReviewOperation | null>(`/api/curation-workspace/candidates/${encodeURIComponent(candidateId)}/review-operation`, signal)
}
export function fetchReviewOperation(id: string, signal?: AbortSignal) {
  return read<ReviewOperation>(`/api/curation-workspace/review-operations/${encodeURIComponent(id)}`, signal)
}
export function controlReview(id: string, action: 'cancel' | 'resume') {
  return command<ReviewOperation>(`review-operations/${encodeURIComponent(id)}/${action}`, {})
}
export function decideValidation(candidateId: string, episodeId: string, choices: HistoryChoice[], action: 'accept' | 'reject', reason: string, requestId: string) {
  return command<unknown>(`candidates/${encodeURIComponent(candidateId)}/validation-decisions`, {
    request_id: requestId, episode_id: episodeId,
    entries: choices.map(choice => ({ ...choice, action, reason })),
  })
}
