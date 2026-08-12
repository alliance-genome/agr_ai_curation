import type { QueryClient } from '@tanstack/react-query'

import {
  buildCurationWorkspaceEnvelopeReviewRowsRequests,
  fetchCurationWorkspaceEnvelopeReviewRows,
} from '@/features/curation/services/curationWorkspaceService'
import type { CurationWorkspace } from '@/features/curation/types'
import { curationWorkspaceEnvelopeReviewRowsQueryKey } from './queryKeys'

export function curationWorkspaceEnvelopeReviewRowsQueryOptions(
  workspace: CurationWorkspace,
) {
  const requests = buildCurationWorkspaceEnvelopeReviewRowsRequests(workspace)

  return {
    queryKey: curationWorkspaceEnvelopeReviewRowsQueryKey(
      workspace.session.session_id,
      requests,
    ),
    queryFn: () => fetchCurationWorkspaceEnvelopeReviewRows(workspace),
    requests,
  }
}

export async function refreshCurationWorkspaceEnvelopeReviewRows(
  queryClient: QueryClient,
  workspace: CurationWorkspace,
): Promise<void> {
  const { requests, ...queryOptions } = curationWorkspaceEnvelopeReviewRowsQueryOptions(workspace)
  if (requests.length === 0) {
    return
  }

  try {
    await queryClient.fetchQuery({
      ...queryOptions,
      staleTime: 0,
    })
  } catch {
    // CurationWorkspacePage's envelopeRowsQuery presents this query-owned error.
  }
}
