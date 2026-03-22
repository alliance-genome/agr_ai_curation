import { keepPreviousData, useQuery } from '@tanstack/react-query'

import type {
  CurationSessionFilters,
  CurationSessionListRequest,
  CurationSessionListResponse,
  CurationSessionStatsRequest,
  CurationSessionStatsResponse,
} from '../types'
import { readCurationApiError } from '../services/api'

function appendStringList(params: URLSearchParams, key: string, values?: string[]) {
  values?.filter(Boolean).forEach((value) => {
    params.append(key, value)
  })
}

function appendFilters(params: URLSearchParams, filters?: CurationSessionFilters) {
  appendStringList(params, 'status', filters?.statuses)
  appendStringList(params, 'adapter_key', filters?.adapter_keys)
  appendStringList(params, 'profile_key', filters?.profile_keys)
  appendStringList(params, 'domain_key', filters?.domain_keys)
  appendStringList(params, 'curator_id', filters?.curator_ids)
  appendStringList(params, 'tag', filters?.tags)

  if (filters?.flow_run_id) {
    params.set('flow_run_id', filters.flow_run_id)
  }

  if (filters?.origin_session_id) {
    params.set('origin_session_id', filters.origin_session_id)
  }

  if (filters?.document_id) {
    params.set('document_id', filters.document_id)
  }

  if (filters?.search?.trim()) {
    params.set('search', filters.search.trim())
  }

  if (filters?.prepared_between?.from_at) {
    params.set('prepared_from', filters.prepared_between.from_at)
  }

  if (filters?.prepared_between?.to_at) {
    params.set('prepared_to', filters.prepared_between.to_at)
  }

  if (filters?.last_worked_between?.from_at) {
    params.set('last_worked_from', filters.last_worked_between.from_at)
  }

  if (filters?.last_worked_between?.to_at) {
    params.set('last_worked_to', filters.last_worked_between.to_at)
  }
}

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
  const params = new URLSearchParams()

  appendFilters(params, request.filters)

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
  const params = new URLSearchParams()
  appendFilters(params, request.filters)
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
