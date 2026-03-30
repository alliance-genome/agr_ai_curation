import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  buildCurationNextSessionQueryParams,
  fetchCurationNextSession,
} from './curationQueueNavigationService'

describe('curationQueueNavigationService', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('serializes queue navigation filters and navigation params', () => {
    const params = buildCurationNextSessionQueryParams({
      current_session_id: 'session-2',
      direction: 'previous',
      filters: {
        statuses: ['in_progress'],
        adapter_keys: ['gene'],
        curator_ids: ['curator-1'],
        tags: ['priority'],
        flow_run_id: 'flow-1',
        document_id: 'doc-1',
        search: '  APOE  ',
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
    })

    expect(params.get('current_session_id')).toBe('session-2')
    expect(params.get('direction')).toBe('previous')
    expect(params.getAll('status')).toEqual(['in_progress'])
    expect(params.getAll('adapter_key')).toEqual(['gene'])
    expect(params.getAll('curator_id')).toEqual(['curator-1'])
    expect(params.getAll('tag')).toEqual(['priority'])
    expect(params.get('flow_run_id')).toBe('flow-1')
    expect(params.get('document_id')).toBe('doc-1')
    expect(params.get('search')).toBe('APOE')
    expect(params.get('prepared_from')).toBe('2026-03-01T00:00:00Z')
    expect(params.get('prepared_to')).toBe('2026-03-10T00:00:00Z')
    expect(params.get('last_worked_from')).toBe('2026-03-11T00:00:00Z')
    expect(params.get('last_worked_to')).toBe('2026-03-20T00:00:00Z')
    expect(params.get('sort_by')).toBe('prepared_at')
    expect(params.get('sort_direction')).toBe('desc')
  })

  it('surfaces API error details from failed queue navigation requests', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: 'Queue unavailable' }), {
          status: 503,
          headers: {
            'Content-Type': 'application/json',
          },
        }),
      ),
    )

    await expect(fetchCurationNextSession({ sort_by: 'prepared_at' })).rejects.toThrow(
      'Queue unavailable',
    )
  })
})
