import { useMutation, useQuery } from '@tanstack/react-query'

import type {
  CurationNextSessionRequest,
  CurationNextSessionResponse,
  CurationQueueContext,
} from '../types'
import { readCurationApiError } from './api'
import { buildCurationSessionFilterQueryParams } from './curationSessionQueryParams'

export type CurationQueueNavigationRequest = Pick<
  CurationNextSessionRequest,
  'filters' | 'sort_by' | 'sort_direction'
>

export interface CurationQueueNavigationState {
  queueRequest: CurationQueueNavigationRequest
  queueContext?: CurationQueueContext | null
}

export function buildCurationQueueNavigationState(
  queueRequest: CurationQueueNavigationRequest,
  queueContext?: CurationQueueContext | null,
): CurationQueueNavigationState {
  return {
    queueRequest,
    queueContext: queueContext ?? null,
  }
}

export function buildCurationQueueNavigationStateFromContext(
  queueContext: CurationQueueContext,
): CurationQueueNavigationState {
  return buildCurationQueueNavigationState(
    {
      filters: queueContext.filters,
      sort_by: queueContext.sort_by,
      sort_direction: queueContext.sort_direction,
    },
    queueContext,
  )
}

export function readCurationQueueNavigationState(
  value: unknown,
): CurationQueueNavigationState | null {
  if (!value || typeof value !== 'object' || !('queueRequest' in value)) {
    return null
  }

  const state = value as CurationQueueNavigationState
  if (!state.queueRequest || typeof state.queueRequest !== 'object') {
    return null
  }

  return {
    queueRequest: {
      filters: state.queueRequest.filters,
      sort_by: state.queueRequest.sort_by,
      sort_direction: state.queueRequest.sort_direction,
    },
    queueContext: state.queueContext ?? null,
  }
}

export function buildCurationNextSessionQueryParams(
  request: CurationNextSessionRequest,
): URLSearchParams {
  const params = buildCurationSessionFilterQueryParams(request.filters)

  if (request.current_session_id) {
    params.set('current_session_id', request.current_session_id)
  }

  if (request.direction) {
    params.set('direction', request.direction)
  }

  if (request.sort_by) {
    params.set('sort_by', request.sort_by)
  }

  if (request.sort_direction) {
    params.set('sort_direction', request.sort_direction)
  }

  return params
}

export async function fetchCurationNextSession(
  request: CurationNextSessionRequest,
): Promise<CurationNextSessionResponse> {
  const params = buildCurationNextSessionQueryParams(request)
  const query = params.toString()
  const response = await fetch(
    `/api/curation-workspace/sessions/next${query ? `?${query}` : ''}`,
    {
      credentials: 'include',
    },
  )

  if (!response.ok) {
    throw new Error(await readCurationApiError(response))
  }

  return response.json() as Promise<CurationNextSessionResponse>
}

export function useCurationNextSessionQuery(
  request: CurationNextSessionRequest,
  options?: { enabled?: boolean },
) {
  return useQuery({
    queryKey: ['curation-next-session', request],
    queryFn: () => fetchCurationNextSession(request),
    enabled: options?.enabled ?? true,
  })
}

export function useCurationNextSessionMutation() {
  return useMutation({
    mutationFn: fetchCurationNextSession,
  })
}
