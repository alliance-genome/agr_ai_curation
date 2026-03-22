import { keepPreviousData, useQuery } from '@tanstack/react-query'

import type {
  CurationSessionListRequest,
  CurationSessionListResponse,
  CurationSessionStatsRequest,
  CurationSessionStatsResponse,
} from '../types'
import { readCurationApiError } from '../services/api'
import { buildCurationSessionFilterQueryParams } from '../services/curationSessionQueryParams'

async function fetchCurationJson<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    credentials: 'include',
  })

  if (!response.ok) {
    throw new Error(await readCurationApiError(response))
  }

  return response.json() as Promise<T>
}

export function buildCurationSessionListQueryParams(
  request: CurationSessionListRequest
): URLSearchParams {
  const params = buildCurationSessionFilterQueryParams(request.filters)

  if (request.sort_by) {
    params.set('sort_by', request.sort_by)
  }

  if (request.sort_direction) {
    params.set('sort_direction', request.sort_direction)
  }

  if (request.page) {
    params.set('page', String(request.page))
  }

  if (request.page_size) {
    params.set('page_size', String(request.page_size))
  }

  if (request.group_by_flow_run) {
    params.set('group_by_flow_run', 'true')
  }

  return params
}

export function buildCurationSessionStatsQueryParams(
  request: CurationSessionStatsRequest
): URLSearchParams {
  return buildCurationSessionFilterQueryParams(request.filters)
}

export async function fetchCurationSessionList(
  request: CurationSessionListRequest
): Promise<CurationSessionListResponse> {
  const params = buildCurationSessionListQueryParams(request)
  const query = params.toString()
  return fetchCurationJson<CurationSessionListResponse>(
    `/api/curation-workspace/sessions${query ? `?${query}` : ''}`
  )
}

export async function fetchCurationSessionStats(
  request: CurationSessionStatsRequest
): Promise<CurationSessionStatsResponse> {
  const params = buildCurationSessionStatsQueryParams(request)
  const query = params.toString()
  return fetchCurationJson<CurationSessionStatsResponse>(
    `/api/curation-workspace/sessions/stats${query ? `?${query}` : ''}`
  )
}

export function useCurationSessionList(request: CurationSessionListRequest) {
  return useQuery({
    queryKey: ['curation-session-list', request],
    queryFn: () => fetchCurationSessionList(request),
    placeholderData: keepPreviousData,
  })
}

export function useCurationSessionStats(request: CurationSessionStatsRequest) {
  return useQuery({
    queryKey: ['curation-session-stats', request],
    queryFn: () => fetchCurationSessionStats(request),
    placeholderData: keepPreviousData,
  })
}
