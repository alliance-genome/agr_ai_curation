import type { CurationSessionFilters } from '../types'

function appendStringList(params: URLSearchParams, key: string, values?: string[]) {
  values?.filter(Boolean).forEach((value) => {
    params.append(key, value)
  })
}

export function appendCurationSessionFilters(
  params: URLSearchParams,
  filters?: CurationSessionFilters,
) {
  appendStringList(params, 'status', filters?.statuses)
  appendStringList(params, 'adapter_key', filters?.adapter_keys)
  appendStringList(params, 'profile_key', filters?.profile_keys)
  appendStringList(params, 'domain_key', filters?.domain_keys)
  appendStringList(params, 'curator_id', filters?.curator_ids)
  appendStringList(params, 'tag', filters?.tags)

  if (filters?.flow_run_id) {
    params.set('flow_run_id', filters.flow_run_id)
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

export function buildCurationSessionFilterQueryParams(
  filters?: CurationSessionFilters,
): URLSearchParams {
  const params = new URLSearchParams()
  appendCurationSessionFilters(params, filters)
  return params
}
