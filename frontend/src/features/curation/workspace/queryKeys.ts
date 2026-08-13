import type { DomainEnvelopeReviewRowsRequest } from '@/features/curation/services/curationWorkspaceService'

export function curationWorkspaceEnvelopeReviewRowsQueryKey(
  sessionId: string,
  requests: readonly DomainEnvelopeReviewRowsRequest[],
) {
  return ['curation-workspace-envelope-review-rows', sessionId, requests] as const
}
