import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  buildCurationFlowRunListQueryParams,
  buildCurationFlowRunSessionsQueryParams,
  buildCurationSessionListQueryParams,
  buildCurationSessionStatsQueryParams,
  createCurationSavedView,
  deleteCurationSavedView,
  fetchCurationFlowRunSessions,
  fetchCurationSavedViews,
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
        curator_ids: ['curator-1'],
        tags: ['priority'],
        flow_run_id: 'flow-1',
        origin_session_id: 'chat-session-1',
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
        saved_view_id: 'saved-view-1',
      },
      sort_by: 'prepared_at',
      sort_direction: 'desc',
      page: 3,
      page_size: 50,
      group_by_flow_run: true,
    })

    expect(params.getAll('status')).toEqual(['new', 'in_progress'])
    expect(params.getAll('adapter_key')).toEqual(['gene'])
    expect(params.getAll('curator_id')).toEqual(['curator-1'])
    expect(params.getAll('tag')).toEqual(['priority'])
    expect(params.get('flow_run_id')).toBe('flow-1')
    expect(params.get('origin_session_id')).toBe('chat-session-1')
    expect(params.get('document_id')).toBe('doc-1')
    expect(params.get('search')).toBe('beta paper')
    expect(params.get('prepared_from')).toBe('2026-03-01T00:00:00Z')
    expect(params.get('prepared_to')).toBe('2026-03-10T00:00:00Z')
    expect(params.get('last_worked_from')).toBe('2026-03-11T00:00:00Z')
    expect(params.get('last_worked_to')).toBe('2026-03-20T00:00:00Z')
    expect(params.get('saved_view_id')).toBe('saved-view-1')
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
        curator_ids: [],
        tags: [],
        flow_run_id: null,
        origin_session_id: null,
        document_id: null,
        search: 'pmid',
        prepared_between: null,
        last_worked_between: null,
        saved_view_id: 'saved-view-2',
      },
    })

    expect(params.getAll('status')).toEqual(['submitted'])
    expect(params.getAll('adapter_key')).toEqual(['gene'])
    expect(params.get('search')).toBe('pmid')
    expect(params.get('saved_view_id')).toBe('saved-view-2')
    expect(params.get('sort_by')).toBeNull()
    expect(params.get('page')).toBeNull()
  })

  it('serializes flow-run list filters into query params', () => {
    const params = buildCurationFlowRunListQueryParams({
      filters: {
        statuses: ['submitted'],
        adapter_keys: ['gene'],
        curator_ids: [],
        tags: [],
        flow_run_id: 'flow-1',
        origin_session_id: 'chat-session-7',
        document_id: null,
        search: 'batch',
        prepared_between: null,
        last_worked_between: null,
        saved_view_id: 'saved-view-3',
      },
    })

    expect(params.getAll('status')).toEqual(['submitted'])
    expect(params.getAll('adapter_key')).toEqual(['gene'])
    expect(params.get('flow_run_id')).toBe('flow-1')
    expect(params.get('origin_session_id')).toBe('chat-session-7')
    expect(params.get('search')).toBe('batch')
    expect(params.get('saved_view_id')).toBe('saved-view-3')
  })

  it('serializes flow-run session pagination into query params', () => {
    const params = buildCurationFlowRunSessionsQueryParams({
      flow_run_id: 'flow alpha',
      filters: {
        statuses: ['in_progress'],
        adapter_keys: [],
        curator_ids: [],
        tags: [],
        flow_run_id: null,
        origin_session_id: 'chat-session-9',
        document_id: null,
        search: null,
        prepared_between: null,
        last_worked_between: null,
        saved_view_id: 'saved-view-4',
      },
      page: 2,
      page_size: 10,
    })

    expect(params.getAll('status')).toEqual(['in_progress'])
    expect(params.get('origin_session_id')).toBe('chat-session-9')
    expect(params.get('saved_view_id')).toBe('saved-view-4')
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
        curator_ids: [],
        tags: [],
        flow_run_id: null,
        origin_session_id: null,
        document_id: null,
        search: null,
        prepared_between: null,
        last_worked_between: null,
        saved_view_id: null,
      },
      page: 1,
      page_size: 25,
    })

    const [url, init] = vi.mocked(global.fetch).mock.calls[0]
    expect(String(url)).toBe('/api/curation-workspace/flow-runs/flow%20alpha/sessions?page=1&page_size=25')
    expect(init?.credentials).toBe('include')
    expect(init?.headers).toBeInstanceOf(Headers)
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

  it('fetches saved views from the curation workspace views endpoint', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            views: [],
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

    await fetchCurationSavedViews()

    const [url, init] = vi.mocked(global.fetch).mock.calls[0]
    expect(String(url)).toBe('/api/curation-workspace/views')
    expect(init?.credentials).toBe('include')
    expect(init?.headers).toBeInstanceOf(Headers)
  })

  it('posts saved view create payloads as JSON', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            view: {
              view_id: 'saved-view-1',
              name: 'My pending sessions',
              description: null,
              filters: {
                statuses: ['in_progress'],
                adapter_keys: ['gene'],
                curator_ids: [],
                tags: [],
                flow_run_id: null,
                origin_session_id: null,
                document_id: null,
                search: null,
                prepared_between: null,
                last_worked_between: null,
                saved_view_id: null,
              },
              sort_by: 'prepared_at',
              sort_direction: 'desc',
              is_default: false,
              created_by: null,
              created_at: '2026-03-22T13:10:00Z',
              updated_at: '2026-03-22T13:10:00Z',
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

    await createCurationSavedView({
      name: 'My pending sessions',
      description: 'Only my active work',
      filters: {
        statuses: ['in_progress'],
        adapter_keys: ['gene'],
        curator_ids: [],
        tags: [],
        flow_run_id: null,
        origin_session_id: null,
        document_id: null,
        search: null,
        prepared_between: null,
        last_worked_between: null,
        saved_view_id: null,
      },
      sort_by: 'prepared_at',
      sort_direction: 'desc',
      is_default: true,
    })

    const [url, init] = vi.mocked(global.fetch).mock.calls[0]
    expect(String(url)).toBe('/api/curation-workspace/views')
    expect(init?.method).toBe('POST')
    expect(init?.credentials).toBe('include')
    expect(init?.headers).toBeInstanceOf(Headers)
    expect((init?.headers as Headers).get('Content-Type')).toBe('application/json')
    expect(init?.body).toBe(
      JSON.stringify({
        name: 'My pending sessions',
        description: 'Only my active work',
        filters: {
          statuses: ['in_progress'],
          adapter_keys: ['gene'],
          curator_ids: [],
          tags: [],
          flow_run_id: null,
          origin_session_id: null,
          document_id: null,
          search: null,
          prepared_between: null,
          last_worked_between: null,
          saved_view_id: null,
        },
        sort_by: 'prepared_at',
        sort_direction: 'desc',
        is_default: true,
      })
    )
  })

  it('deletes saved views through the curation workspace endpoint', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            deleted_view_id: 'saved-view-1',
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

    await deleteCurationSavedView('saved-view-1')

    const [url, init] = vi.mocked(global.fetch).mock.calls[0]
    expect(String(url)).toBe('/api/curation-workspace/views/saved-view-1')
    expect(init?.method).toBe('DELETE')
    expect(init?.credentials).toBe('include')
    expect(init?.headers).toBeInstanceOf(Headers)
  })
})
