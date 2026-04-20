import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import {
  buildChatHistoryDetailQueryParams,
  buildChatHistoryListQueryParams,
  bulkDeleteChatSessions,
  deleteChatSession,
  fetchChatHistoryDetail,
  fetchChatHistoryList,
  renameChatSession,
} from './chatHistoryApi'

const mockFetch = vi.fn()

describe('chatHistoryApi', () => {
  beforeEach(() => {
    mockFetch.mockReset()
    vi.stubGlobal('fetch', mockFetch)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('serializes list filters into chat history query params', () => {
    const params = buildChatHistoryListQueryParams({
      limit: 50,
      cursor: ' cursor-1 ',
      query: '  TP53 sessions  ',
      documentId: ' doc-1 ',
    })

    expect(params.get('limit')).toBe('50')
    expect(params.get('cursor')).toBe('cursor-1')
    expect(params.get('query')).toBe('TP53 sessions')
    expect(params.get('document_id')).toBe('doc-1')
  })

  it('serializes detail pagination into chat history detail query params', () => {
    const params = buildChatHistoryDetailQueryParams({
      sessionId: 'session-1',
      messageLimit: 25,
      messageCursor: ' message-cursor-2 ',
    })

    expect(params.get('message_limit')).toBe('25')
    expect(params.get('message_cursor')).toBe('message-cursor-2')
  })

  it('fetches chat history list responses with credentials included', async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          total_sessions: 1,
          limit: 10,
          query: 'TP53',
          document_id: null,
          next_cursor: 'cursor-2',
          sessions: [],
        }),
        {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
          },
        },
      ),
    )

    await fetchChatHistoryList({
      limit: 10,
      cursor: 'cursor-1',
      query: 'TP53',
    })

    const [url, init] = mockFetch.mock.calls[0]
    expect(String(url)).toBe('/api/chat/history?limit=10&cursor=cursor-1&query=TP53')
    expect(init?.credentials).toBe('include')
    expect(init?.headers).toBeInstanceOf(Headers)
  })

  it('fetches encoded session detail responses', async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          session: {
            session_id: 'session alpha',
            created_at: '2026-04-20T00:00:00Z',
            updated_at: '2026-04-20T00:00:00Z',
            recent_activity_at: '2026-04-20T00:00:00Z',
          },
          active_document: null,
          messages: [],
          message_limit: 25,
          next_message_cursor: null,
        }),
        {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
          },
        },
      ),
    )

    await fetchChatHistoryDetail({
      sessionId: 'session alpha',
      messageLimit: 25,
      messageCursor: 'message-cursor-1',
    })

    const [url, init] = mockFetch.mock.calls[0]
    expect(String(url)).toBe(
      '/api/chat/history/session%20alpha?message_limit=25&message_cursor=message-cursor-1',
    )
    expect(init?.credentials).toBe('include')
  })

  it('posts rename payloads as JSON to the chat session endpoint', async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          session: {
            session_id: 'session alpha',
            title: 'Renamed session',
            created_at: '2026-04-20T00:00:00Z',
            updated_at: '2026-04-20T00:00:00Z',
            recent_activity_at: '2026-04-20T00:00:00Z',
          },
        }),
        {
          status: 200,
          headers: {
            'Content-Type': 'application/json',
          },
        },
      ),
    )

    await renameChatSession({
      sessionId: 'session alpha',
      title: 'Renamed session',
    })

    const [url, init] = mockFetch.mock.calls[0]
    expect(String(url)).toBe('/api/chat/session/session%20alpha')
    expect(init?.method).toBe('PATCH')
    expect(init?.body).toBe(JSON.stringify({ title: 'Renamed session' }))
    expect(init?.headers).toBeInstanceOf(Headers)
    expect((init?.headers as Headers).get('Content-Type')).toBe('application/json')
  })

  it('handles 204 delete responses without attempting JSON parsing', async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(null, {
        status: 204,
      }),
    )

    await expect(
      deleteChatSession({
        sessionId: 'session-1',
      }),
    ).resolves.toBeUndefined()

    const [url, init] = mockFetch.mock.calls[0]
    expect(String(url)).toBe('/api/chat/session/session-1')
    expect(init?.method).toBe('DELETE')
  })

  it('surfaces bulk delete API errors', async () => {
    mockFetch.mockResolvedValueOnce(
      new Response(
        JSON.stringify({
          detail: 'Failed to delete chat sessions',
        }),
        {
          status: 500,
          headers: {
            'Content-Type': 'application/json',
          },
        },
      ),
    )

    await expect(
      bulkDeleteChatSessions({
        sessionIds: ['session-1', 'session-2'],
      }),
    ).rejects.toThrow('Failed to delete chat sessions')
  })
})
