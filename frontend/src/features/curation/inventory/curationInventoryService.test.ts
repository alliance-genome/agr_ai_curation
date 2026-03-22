import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  buildCurationFlowRunListQueryParams,
  buildCurationFlowRunSessionsQueryParams,
  buildCurationSessionListQueryParams,
  buildCurationSessionStatsQueryParams,
  fetchCurationFlowRunSessions,
  fetchCurationSessionList,
} from './curationInventoryService'

describe('curationInventoryService', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('serializes list request filters, sorting, and pagination into query params', () => {
    const params = buildCurationSessionListQueryParams({
      filters: {
        statuses: ['new', 'in_progress'],
        adapter_keys: ['gene'],
        profile_keys: ['alpha'],
        domain_keys: [],
        curator_ids: ['curator-1'],
        tags: ['priority'],
        flow_run_id: 'flow-1',
        document_id: 'doc-1',
        search: '  beta paper  ',
        prepared_between: {
          from_at: '2026-03-01T00:00:00Z',
          to_at: '2026-03-10T00:00:00Z',
        },
        last_worked_between: {
          from_at: '2026-03-11T00:00:00Z',
          to_at: '2026-03-20T00:00:00Z',
        },
        saved_view_id: null,
      },
      sort_by: 'prepared_at',
      sort_direction: 'desc',
      page: 3,
      page_size: 50,
      group_by_flow_run: true,
    })

    expect(params.getAll('status')).toEqual(['new', 'in_progress'])
    expect(params.getAll('adapter_key')).toEqual(['gene'])
    expect(params.getAll('profile_key')).toEqual(['alpha'])
    expect(params.getAll('curator_id')).toEqual(['curator-1'])
    expect(params.getAll('tag')).toEqual(['priority'])
    expect(params.get('flow_run_id')).toBe('flow-1')
    expect(params.get('document_id')).toBe('doc-1')
    expect(params.get('search')).toBe('beta paper')
    expect(params.get('prepared_from')).toBe('2026-03-01T00:00:00Z')
    expect(params.get('prepared_to')).toBe('2026-03-10T00:00:00Z')
    expect(params.get('last_worked_from')).toBe('2026-03-11T00:00:00Z')
    expect(params.get('last_worked_to')).toBe('2026-03-20T00:00:00Z')
    expect(params.get('sort_by')).toBe('prepared_at')
    expect(params.get('sort_direction')).toBe('desc')
    expect(params.get('page')).toBe('3')
    expect(params.get('page_size')).toBe('50')
    expect(params.get('group_by_flow_run')).toBe('true')
  })

  it('serializes stats filters without list-only params', () => {
    const params = buildCurationSessionStatsQueryParams({
      filters: {
        statuses: ['submitted'],
        adapter_keys: ['gene'],
        profile_keys: [],
        domain_keys: [],
        curator_ids: [],
        tags: [],
        flow_run_id: null,
        document_id: null,
        search: 'pmid',
        prepared_between: null,
        last_worked_between: null,
        saved_view_id: null,
      },
    })

    expect(params.getAll('status')).toEqual(['submitted'])
    expect(params.getAll('adapter_key')).toEqual(['gene'])
    expect(params.get('search')).toBe('pmid')
    expect(params.get('sort_by')).toBeNull()
    expect(params.get('page')).toBeNull()
  })

  it('serializes flow-run list filters into query params', () => {
    const params = buildCurationFlowRunListQueryParams({
      filters: {
        statuses: ['submitted'],
        adapter_keys: ['gene'],
        profile_keys: [],
        domain_keys: [],
        curator_ids: [],
        tags: [],
        flow_run_id: 'flow-1',
        document_id: null,
        search: 'batch',
        prepared_between: null,
        last_worked_between: null,
        saved_view_id: null,
      },
    })

    expect(params.getAll('status')).toEqual(['submitted'])
    expect(params.getAll('adapter_key')).toEqual(['gene'])
    expect(params.get('flow_run_id')).toBe('flow-1')
    expect(params.get('search')).toBe('batch')
  })

  it('serializes flow-run session pagination into query params', () => {
    const params = buildCurationFlowRunSessionsQueryParams({
      flow_run_id: 'flow alpha',
      filters: {
        statuses: ['in_progress'],
        adapter_keys: [],
        profile_keys: [],
        domain_keys: [],
        curator_ids: [],
        tags: [],
        flow_run_id: null,
        document_id: null,
        search: null,
        prepared_between: null,
        last_worked_between: null,
        saved_view_id: null,
      },
      page: 2,
      page_size: 10,
    })

    expect(params.getAll('status')).toEqual(['in_progress'])
    expect(params.get('page')).toBe('2')
    expect(params.get('page_size')).toBe('10')
  })

  it('encodes the flow-run id when fetching grouped sessions', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            flow_run: {
              flow_run_id: 'flow alpha',
              display_label: 'flow alpha',
              session_count: 1,
              reviewed_count: 0,
              pending_count: 1,
              submitted_count: 0,
              last_activity_at: '2026-03-20T00:00:00Z',
            },
            sessions: [],
            page_info: {
              page: 1,
              page_size: 25,
              total_items: 0,
              total_pages: 0,
              has_next_page: false,
              has_previous_page: false,
            },
          }),
          {
            status: 200,
            headers: {
              'Content-Type': 'application/json',
            },
          }
        )
      )
    )

    await fetchCurationFlowRunSessions({
      flow_run_id: 'flow alpha',
      filters: {
        statuses: [],
        adapter_keys: [],
        profile_keys: [],
        domain_keys: [],
        curator_ids: [],
        tags: [],
        flow_run_id: null,
        document_id: null,
        search: null,
        prepared_between: null,
        last_worked_between: null,
        saved_view_id: null,
      },
      page: 1,
      page_size: 25,
    })

    expect(vi.mocked(global.fetch)).toHaveBeenCalledWith(
      '/api/curation-workspace/flow-runs/flow%20alpha/sessions?page=1&page_size=25',
      { credentials: 'include' }
    )
  })

  it('surfaces API error details from failed list requests', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'Inventory unavailable' }), {
          status: 503,
          headers: {
            'Content-Type': 'application/json',
          },
        })
      )
    )

    await expect(fetchCurationSessionList({})).rejects.toThrow('Inventory unavailable')
  })
})
