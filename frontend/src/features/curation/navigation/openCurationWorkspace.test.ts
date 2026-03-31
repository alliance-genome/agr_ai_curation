import { beforeEach, describe, expect, it, vi } from 'vitest'

import {
  getCurationWorkspaceLaunchAvailability,
  normalizeCurationWorkspaceScopeValues,
  openCurationWorkspace,
  resolveCurationWorkspaceSessionId,
} from './openCurationWorkspace'

function buildSessionListResponse(sessionIds: string[]) {
  return {
    sessions: sessionIds.map((sessionId) => ({
      session_id: sessionId,
    })),
    page_info: {
      page: 1,
      page_size: 1,
      total_items: sessionIds.length,
      total_pages: sessionIds.length > 0 ? 1 : 0,
      has_next_page: false,
      has_previous_page: false,
    },
    applied_filters: {},
    sort_by: 'prepared_at',
    sort_direction: 'desc',
    flow_run_groups: [],
  }
}

describe('openCurationWorkspace', () => {
  beforeEach(() => {
    vi.mocked(global.fetch).mockReset()
  })

  it('normalizes launch scope values by trimming blanks and removing duplicates', () => {
    expect(
      normalizeCurationWorkspaceScopeValues([' gene ', '', 'gene', 'disease', 'disease '])
    ).toEqual(['gene', 'disease'])
  })

  it('navigates directly when the launch target already includes a session id', async () => {
    const navigate = vi.fn()

    await openCurationWorkspace({
      sessionId: 'session-direct',
      navigate,
    })

    expect(global.fetch).not.toHaveBeenCalled()
    expect(navigate).toHaveBeenCalledWith('/curation/session-direct')
  })

  it('navigates directly to an existing curation session when one already exists', async () => {
    vi.mocked(global.fetch).mockResolvedValueOnce(
      new Response(JSON.stringify(buildSessionListResponse(['session-existing'])), {
        status: 200,
        headers: {
          'Content-Type': 'application/json',
        },
      })
    )

    const navigate = vi.fn()

    await openCurationWorkspace({
      documentId: 'doc-1',
      originSessionId: 'chat-session-1',
      adapterKeys: ['gene'],
      navigate,
    })

    expect(global.fetch).toHaveBeenCalledTimes(1)

    const [requestUrl, requestInit] = vi.mocked(global.fetch).mock.calls[0]
    const parsedUrl = new URL(String(requestUrl), 'http://localhost')

    expect(parsedUrl.pathname).toBe('/api/curation-workspace/sessions')
    expect(parsedUrl.searchParams.get('document_id')).toBe('doc-1')
    expect(parsedUrl.searchParams.get('origin_session_id')).toBe('chat-session-1')
    expect(parsedUrl.searchParams.getAll('adapter_key')).toEqual(['gene'])
    expect(parsedUrl.searchParams.get('sort_by')).toBe('prepared_at')
    expect(parsedUrl.searchParams.get('sort_direction')).toBe('desc')
    expect(parsedUrl.searchParams.get('page')).toBe('1')
    expect(parsedUrl.searchParams.get('page_size')).toBe('1')
    expect(requestInit).toEqual(
      expect.objectContaining({
        credentials: 'include',
      })
    )
    expect(navigate).toHaveBeenCalledWith('/curation/session-existing')
  })

  it('bootstraps a session when no matching session exists for the document and flow run', async () => {
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(
        new Response(JSON.stringify(buildSessionListResponse([])), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
          },
        })
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            created: true,
            session: {
              session_id: 'session-bootstrapped',
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

    const navigate = vi.fn()

    await openCurationWorkspace({
      documentId: 'doc-1',
      flowRunId: 'flow-7',
      originSessionId: 'chat-session-7',
      adapterKeys: ['gene'],
      navigate,
    })

    expect(global.fetch).toHaveBeenCalledTimes(2)

    const [listUrl] = vi.mocked(global.fetch).mock.calls[0]
    const parsedListUrl = new URL(String(listUrl), 'http://localhost')
    expect(parsedListUrl.searchParams.get('document_id')).toBe('doc-1')
    expect(parsedListUrl.searchParams.get('flow_run_id')).toBe('flow-7')
    expect(parsedListUrl.searchParams.get('origin_session_id')).toBe('chat-session-7')
    expect(parsedListUrl.searchParams.getAll('adapter_key')).toEqual(['gene'])

    const [bootstrapUrl, bootstrapInit] = vi.mocked(global.fetch).mock.calls[1]
    expect(String(bootstrapUrl)).toBe('/api/curation-workspace/documents/doc-1/bootstrap')
    expect(bootstrapInit).toEqual({
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        adapter_key: 'gene',
        flow_run_id: 'flow-7',
        origin_session_id: 'chat-session-7',
      }),
    })
    expect(navigate).toHaveBeenCalledWith('/curation/session-bootstrapped')
  })

  it('surfaces bootstrap errors when no prepared session can be created', async () => {
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(
        new Response(JSON.stringify(buildSessionListResponse([])), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
          },
        })
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            detail: 'No prepared curation session is available for this document.',
          }),
          {
            status: 404,
            headers: {
              'Content-Type': 'application/json',
            },
          }
        )
      )

    await expect(
      resolveCurationWorkspaceSessionId({
        documentId: 'doc-1',
        flowRunId: 'flow-7',
      })
    ).rejects.toThrow('No prepared curation session is available for this document.')
  })

  it('reports launch availability when no session exists yet but bootstrap is possible', async () => {
    vi.mocked(global.fetch)
      .mockResolvedValueOnce(
        new Response(JSON.stringify(buildSessionListResponse([])), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
          },
        })
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ eligible: true }), {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
          },
        })
      )

    const availability = await getCurationWorkspaceLaunchAvailability({
      documentId: 'doc-1',
      flowRunId: 'flow-7',
      originSessionId: 'chat-session-7',
      adapterKeys: ['gene'],
    })

    expect(availability).toEqual({
      existingSessionId: null,
      canBootstrap: true,
    })

    const [availabilityUrl, availabilityInit] = vi.mocked(global.fetch).mock.calls[1]
    const parsedAvailabilityUrl = new URL(String(availabilityUrl), 'http://localhost')
    expect(parsedAvailabilityUrl.pathname).toBe(
      '/api/curation-workspace/documents/doc-1/bootstrap-availability'
    )
    expect(parsedAvailabilityUrl.searchParams.get('flow_run_id')).toBe('flow-7')
    expect(parsedAvailabilityUrl.searchParams.get('origin_session_id')).toBe('chat-session-7')
    expect(parsedAvailabilityUrl.searchParams.get('adapter_key')).toBe('gene')
    expect(availabilityInit).toEqual({
      credentials: 'include',
    })
  })
})
