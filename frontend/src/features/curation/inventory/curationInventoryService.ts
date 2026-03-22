import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'

import type {
  CurationFlowRunListRequest,
  CurationFlowRunListResponse,
  CurationFlowRunSessionsRequest,
  CurationFlowRunSessionsResponse,
  CurationSavedViewCreateRequest,
  CurationSavedViewCreateResponse,
  CurationSavedViewDeleteResponse,
  CurationSavedViewListResponse,
  CurationSessionListRequest,
  CurationSessionListResponse,
  CurationSessionStatsRequest,
  CurationSessionStatsResponse,
} from '../types'
import { readCurationApiError } from '../services/api'
import { buildCurationSessionFilterQueryParams } from '../services/curationSessionQueryParams'

interface CurationInventoryQueryOptions {
  enabled?: boolean
}

export const CURATION_SAVED_VIEWS_QUERY_KEY = ['curation-saved-views'] as const

async function fetchCurationJson<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(path, {
    credentials: 'include',
    ...init,
    headers,
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

export function buildCurationFlowRunListQueryParams(
  request: CurationFlowRunListRequest
): URLSearchParams {
  return buildCurationSessionFilterQueryParams(request.filters)
}

export function buildCurationFlowRunSessionsQueryParams(
  request: CurationFlowRunSessionsRequest
): URLSearchParams {
  const params = buildCurationSessionFilterQueryParams(request.filters)

  if (request.page) {
    params.set('page', String(request.page))
  }

  if (request.page_size) {
    params.set('page_size', String(request.page_size))
  }

  return params
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

export async function fetchCurationSavedViews(): Promise<CurationSavedViewListResponse> {
  return fetchCurationJson<CurationSavedViewListResponse>('/api/curation-workspace/views')
}

export async function createCurationSavedView(
  request: CurationSavedViewCreateRequest
): Promise<CurationSavedViewCreateResponse> {
  return fetchCurationJson<CurationSavedViewCreateResponse>('/api/curation-workspace/views', {
    method: 'POST',
    body: JSON.stringify(request),
  })
}

export async function deleteCurationSavedView(
  viewId: string
): Promise<CurationSavedViewDeleteResponse> {
  return fetchCurationJson<CurationSavedViewDeleteResponse>(
    `/api/curation-workspace/views/${viewId}`,
    {
      method: 'DELETE',
    }
  )
}

export async function fetchCurationFlowRunList(
  request: CurationFlowRunListRequest
): Promise<CurationFlowRunListResponse> {
  const params = buildCurationFlowRunListQueryParams(request)
  const query = params.toString()
  return fetchCurationJson<CurationFlowRunListResponse>(
    `/api/curation-workspace/flow-runs${query ? `?${query}` : ''}`
  )
}

export async function fetchCurationFlowRunSessions(
  request: CurationFlowRunSessionsRequest
): Promise<CurationFlowRunSessionsResponse> {
  const params = buildCurationFlowRunSessionsQueryParams(request)
  const query = params.toString()
  return fetchCurationJson<CurationFlowRunSessionsResponse>(
    `/api/curation-workspace/flow-runs/${encodeURIComponent(request.flow_run_id)}/sessions${
      query ? `?${query}` : ''
    }`
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

export function useCurationSavedViews() {
  return useQuery({
    queryKey: CURATION_SAVED_VIEWS_QUERY_KEY,
    queryFn: fetchCurationSavedViews,
  })
}

export function useCreateCurationSavedView() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: createCurationSavedView,
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: CURATION_SAVED_VIEWS_QUERY_KEY,
      })
    },
  })
}

export function useDeleteCurationSavedView() {
  const queryClient = useQueryClient()

  return useMutation({
    mutationFn: deleteCurationSavedView,
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: CURATION_SAVED_VIEWS_QUERY_KEY,
      })
    },
  })
}

export function useCurationFlowRunList(
  request: CurationFlowRunListRequest,
  options: CurationInventoryQueryOptions = {}
) {
  return useQuery({
    queryKey: ['curation-flow-run-list', request],
    queryFn: () => fetchCurationFlowRunList(request),
    placeholderData: keepPreviousData,
    enabled: options.enabled,
  })
}

export function useCurationFlowRunSessions(
  request: CurationFlowRunSessionsRequest,
  options: CurationInventoryQueryOptions = {}
) {
  return useQuery({
    queryKey: ['curation-flow-run-sessions', request],
    queryFn: () => fetchCurationFlowRunSessions(request),
    placeholderData: keepPreviousData,
    enabled: options.enabled,
  })
}
