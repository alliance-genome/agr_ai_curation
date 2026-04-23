import { act, renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { chatCacheKeys } from '@/lib/chatCacheKeys'

import {
  useBulkDeleteChatSessionsMutation,
  useChatHistoryDetailQuery,
  useChatHistoryListQuery,
  useChatHistoryTranscriptQuery,
  useDeleteChatSessionMutation,
  useRenameChatSessionMutation,
} from './useChatHistoryQuery'

const serviceMocks = vi.hoisted(() => ({
  fetchChatHistoryList: vi.fn(),
  fetchChatHistoryDetail: vi.fn(),
  renameChatSession: vi.fn(),
  deleteChatSession: vi.fn(),
  bulkDeleteChatSessions: vi.fn(),
}))

vi.mock('@/services/chatHistoryApi', () => ({
  fetchChatHistoryList: serviceMocks.fetchChatHistoryList,
  fetchChatHistoryDetail: serviceMocks.fetchChatHistoryDetail,
  renameChatSession: serviceMocks.renameChatSession,
  deleteChatSession: serviceMocks.deleteChatSession,
  bulkDeleteChatSessions: serviceMocks.bulkDeleteChatSessions,
}))

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        gcTime: Infinity,
      },
      mutations: {
        retry: false,
      },
    },
  })
}

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  }
}

describe('useChatHistoryQuery', () => {
  beforeEach(() => {
    serviceMocks.fetchChatHistoryList.mockReset()
    serviceMocks.fetchChatHistoryDetail.mockReset()
    serviceMocks.renameChatSession.mockReset()
    serviceMocks.deleteChatSession.mockReset()
    serviceMocks.bulkDeleteChatSessions.mockReset()
  })

  afterEach(() => {
    vi.clearAllMocks()
  })

  it('stores list query results under chat history list cache keys', async () => {
    const queryClient = createQueryClient()
    const response = {
      chat_kind: 'assistant_chat',
      total_sessions: 1,
      limit: 20,
      query: null,
      document_id: null,
      next_cursor: null,
      sessions: [
        {
          session_id: 'session-1',
          chat_kind: 'assistant_chat',
          title: 'First session',
          created_at: '2026-04-20T00:00:00Z',
          updated_at: '2026-04-20T00:00:00Z',
          recent_activity_at: '2026-04-20T00:00:00Z',
        },
      ],
    }

    serviceMocks.fetchChatHistoryList.mockResolvedValue(response)

    const { result } = renderHook(
      () =>
        useChatHistoryListQuery({
          chatKind: 'assistant_chat',
          query: ' session search ',
        }),
      {
        wrapper: createWrapper(queryClient),
      },
    )

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(result.current.data).toEqual(response)
    expect(
      queryClient.getQueryData(
        chatCacheKeys.history.list({
          chatKind: 'assistant_chat',
          query: 'session search',
        }),
      ),
    ).toEqual(response)
  })

  it('keeps all-history and single-kind list caches separate', async () => {
    const queryClient = createQueryClient()
    const allResponse = {
      chat_kind: 'all',
      total_sessions: 2,
      limit: 20,
      query: 'workflow',
      document_id: null,
      next_cursor: null,
      sessions: [
        {
          session_id: 'session-1',
          chat_kind: 'assistant_chat',
          title: 'Assistant workflow review',
          created_at: '2026-04-20T00:00:00Z',
          updated_at: '2026-04-20T00:00:00Z',
          recent_activity_at: '2026-04-20T00:00:00Z',
        },
        {
          session_id: 'session-2',
          chat_kind: 'agent_studio',
          title: 'Agent workflow review',
          created_at: '2026-04-20T00:00:00Z',
          updated_at: '2026-04-20T00:00:00Z',
          recent_activity_at: '2026-04-20T00:00:00Z',
        },
      ],
    }
    const agentStudioResponse = {
      chat_kind: 'agent_studio',
      total_sessions: 1,
      limit: 20,
      query: 'workflow',
      document_id: null,
      next_cursor: null,
      sessions: [
        {
          session_id: 'session-2',
          chat_kind: 'agent_studio',
          title: 'Agent workflow review',
          created_at: '2026-04-20T00:00:00Z',
          updated_at: '2026-04-20T00:00:00Z',
          recent_activity_at: '2026-04-20T00:00:00Z',
        },
      ],
    }

    serviceMocks.fetchChatHistoryList
      .mockResolvedValueOnce(allResponse)
      .mockResolvedValueOnce(agentStudioResponse)

    const wrapper = createWrapper(queryClient)

    const { result: allResult } = renderHook(
      () =>
        useChatHistoryListQuery({
          chatKind: 'all',
          query: ' workflow ',
        }),
      { wrapper },
    )

    await waitFor(() => {
      expect(allResult.current.isSuccess).toBe(true)
    })

    const { result: agentStudioResult } = renderHook(
      () =>
        useChatHistoryListQuery({
          chatKind: 'agent_studio',
          query: ' workflow ',
        }),
      { wrapper },
    )

    await waitFor(() => {
      expect(agentStudioResult.current.isSuccess).toBe(true)
    })

    expect(
      queryClient.getQueryData(
        chatCacheKeys.history.list({
          chatKind: 'all',
          query: 'workflow',
        }),
      ),
    ).toEqual(allResponse)
    expect(
      queryClient.getQueryData(
        chatCacheKeys.history.list({
          chatKind: 'agent_studio',
          query: 'workflow',
        }),
      ),
    ).toEqual(agentStudioResponse)
  })

  it('stores detail query results under session-scoped cache keys', async () => {
    const queryClient = createQueryClient()
    const response = {
      session: {
        session_id: 'session-1',
        chat_kind: 'assistant_chat',
        title: 'Stored session',
        created_at: '2026-04-20T00:00:00Z',
        updated_at: '2026-04-20T00:00:00Z',
        recent_activity_at: '2026-04-20T00:00:00Z',
      },
      active_document: null,
      messages: [],
      message_limit: 50,
      next_message_cursor: null,
    }

    serviceMocks.fetchChatHistoryDetail.mockResolvedValue(response)

    const { result } = renderHook(
      () =>
        useChatHistoryDetailQuery({
          sessionId: 'session-1',
          messageLimit: 50,
        }),
      {
        wrapper: createWrapper(queryClient),
      },
    )

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(result.current.data).toEqual(response)
    expect(
      queryClient.getQueryData(
        chatCacheKeys.history.detail({
          sessionId: 'session-1',
          messageLimit: 50,
        }),
      ),
    ).toEqual(response)
  })

  it('aggregates paginated transcript pages for durable chat hydration', async () => {
    const queryClient = createQueryClient()

    serviceMocks.fetchChatHistoryDetail
      .mockResolvedValueOnce({
        session: {
          session_id: 'session-1',
          title: 'Stored session',
          created_at: '2026-04-20T00:00:00Z',
          updated_at: '2026-04-20T00:00:00Z',
          recent_activity_at: '2026-04-20T00:00:00Z',
        },
        active_document: null,
        messages: [
          {
            message_id: 'message-1',
            session_id: 'session-1',
            chat_kind: 'assistant_chat',
            turn_id: 'turn-1',
            role: 'user',
            message_type: 'text',
            content: 'First question',
            payload_json: null,
            trace_id: null,
            created_at: '2026-04-20T00:00:01Z',
          },
        ],
        message_limit: 200,
        next_message_cursor: 'cursor-2',
      })
      .mockResolvedValueOnce({
        session: {
          session_id: 'session-1',
          chat_kind: 'assistant_chat',
          title: 'Stored session',
          created_at: '2026-04-20T00:00:00Z',
          updated_at: '2026-04-20T00:00:00Z',
          recent_activity_at: '2026-04-20T00:00:00Z',
        },
        active_document: null,
        messages: [
          {
            message_id: 'message-2',
            session_id: 'session-1',
            chat_kind: 'assistant_chat',
            turn_id: 'turn-1',
            role: 'assistant',
            message_type: 'text',
            content: 'First answer',
            payload_json: null,
            trace_id: 'trace-1',
            created_at: '2026-04-20T00:00:02Z',
          },
        ],
        message_limit: 200,
        next_message_cursor: null,
      })

    const { result } = renderHook(
      () =>
        useChatHistoryTranscriptQuery({
          sessionId: 'session-1',
        }),
      {
        wrapper: createWrapper(queryClient),
      },
    )

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(serviceMocks.fetchChatHistoryDetail).toHaveBeenCalledTimes(2)
    expect(serviceMocks.fetchChatHistoryDetail).toHaveBeenNthCalledWith(
      1,
      {
        sessionId: 'session-1',
        messageLimit: 200,
        messageCursor: null,
      },
    )
    expect(serviceMocks.fetchChatHistoryDetail).toHaveBeenNthCalledWith(2, {
      sessionId: 'session-1',
      messageLimit: 200,
      messageCursor: 'cursor-2',
    })
    expect(result.current.data?.messages).toEqual([
      expect.objectContaining({
        message_id: 'message-1',
        content: 'First question',
      }),
      expect.objectContaining({
        message_id: 'message-2',
        content: 'First answer',
      }),
    ])
  })

  it('fails transcript hydration after too many sequential pages', async () => {
    const queryClient = createQueryClient()

    serviceMocks.fetchChatHistoryDetail.mockImplementation(async ({ messageCursor }) => {
      const pageNumber = messageCursor
        ? Number(messageCursor.replace('cursor-', ''))
        : 1

      return {
        session: {
          session_id: 'session-1',
          chat_kind: 'assistant_chat',
          title: 'Stored session',
          created_at: '2026-04-20T00:00:00Z',
          updated_at: '2026-04-20T00:00:00Z',
          recent_activity_at: '2026-04-20T00:00:00Z',
        },
        active_document: null,
        messages: [
          {
            message_id: `message-${pageNumber}`,
            session_id: 'session-1',
            chat_kind: 'assistant_chat',
            turn_id: `turn-${pageNumber}`,
            role: 'assistant',
            message_type: 'text',
            content: `Page ${pageNumber}`,
            payload_json: null,
            trace_id: `trace-${pageNumber}`,
            created_at: '2026-04-20T00:00:02Z',
          },
        ],
        message_limit: 200,
        next_message_cursor: pageNumber >= 51 ? null : `cursor-${pageNumber + 1}`,
      }
    })

    const { result } = renderHook(
      () =>
        useChatHistoryTranscriptQuery({
          sessionId: 'session-1',
        }),
      {
        wrapper: createWrapper(queryClient),
      },
    )

    await waitFor(() => {
      expect(result.current.isError).toBe(true)
    })

    expect(serviceMocks.fetchChatHistoryDetail).toHaveBeenCalledTimes(50)
    expect(result.current.error?.message).toBe(
      'Exceeded 50 transcript pages for session session-1',
    )
  })

  it('invalidates list and detail caches after rename mutations', async () => {
    const queryClient = createQueryClient()
    const invalidateQueriesSpy = vi.spyOn(queryClient, 'invalidateQueries')

    serviceMocks.renameChatSession.mockResolvedValue({
      session: {
        session_id: 'session-1',
        chat_kind: 'assistant_chat',
        title: 'Renamed session',
        created_at: '2026-04-20T00:00:00Z',
        updated_at: '2026-04-20T00:00:00Z',
        recent_activity_at: '2026-04-20T00:00:00Z',
      },
    })

    const { result } = renderHook(() => useRenameChatSessionMutation(), {
      wrapper: createWrapper(queryClient),
    })

    await act(async () => {
      await result.current.mutateAsync({
        sessionId: 'session-1',
        title: 'Renamed session',
      })
    })

    expect(invalidateQueriesSpy).toHaveBeenCalledWith({
      queryKey: chatCacheKeys.history.lists(),
    })
    expect(invalidateQueriesSpy).toHaveBeenCalledWith({
      queryKey: chatCacheKeys.history.detailSession('session-1'),
    })
  })

  it('invalidates lists and removes session detail caches after delete mutations', async () => {
    const queryClient = createQueryClient()
    const invalidateQueriesSpy = vi.spyOn(queryClient, 'invalidateQueries')

    queryClient.setQueryData(
      chatCacheKeys.history.detail({
        sessionId: 'session-1',
      }),
      { session: { session_id: 'session-1' } },
    )
    queryClient.setQueryData(
      chatCacheKeys.history.detail({
        sessionId: 'session-1',
        messageLimit: 25,
      }),
      { session: { session_id: 'session-1' } },
    )

    serviceMocks.deleteChatSession.mockResolvedValue(undefined)

    const { result } = renderHook(() => useDeleteChatSessionMutation(), {
      wrapper: createWrapper(queryClient),
    })

    await act(async () => {
      await result.current.mutateAsync({
        sessionId: 'session-1',
      })
    })

    expect(invalidateQueriesSpy).toHaveBeenCalledWith({
      queryKey: chatCacheKeys.history.lists(),
    })
    expect(
      queryClient.getQueryCache().findAll({
        queryKey: chatCacheKeys.history.detailSession('session-1'),
      }),
    ).toHaveLength(0)
  })

  it('invalidates lists and removes deleted session detail caches after bulk delete mutations', async () => {
    const queryClient = createQueryClient()
    const invalidateQueriesSpy = vi.spyOn(queryClient, 'invalidateQueries')

    queryClient.setQueryData(
      chatCacheKeys.history.detail({
        sessionId: 'session-1',
      }),
      { session: { session_id: 'session-1' } },
    )
    queryClient.setQueryData(
      chatCacheKeys.history.detail({
        sessionId: 'session-2',
      }),
      { session: { session_id: 'session-2' } },
    )
    queryClient.setQueryData(
      chatCacheKeys.history.detail({
        sessionId: 'session-3',
      }),
      { session: { session_id: 'session-3' } },
    )

    serviceMocks.bulkDeleteChatSessions.mockResolvedValue({
      requested_count: 3,
      deleted_count: 2,
      deleted_session_ids: ['session-1', 'session-2'],
    })

    const { result } = renderHook(() => useBulkDeleteChatSessionsMutation(), {
      wrapper: createWrapper(queryClient),
    })

    await act(async () => {
      await result.current.mutateAsync({
        sessionIds: ['session-1', 'session-2', 'session-3'],
      })
    })

    expect(invalidateQueriesSpy).toHaveBeenCalledWith({
      queryKey: chatCacheKeys.history.lists(),
    })
    expect(
      queryClient.getQueryCache().findAll({
        queryKey: chatCacheKeys.history.detailSession('session-1'),
      }),
    ).toHaveLength(0)
    expect(
      queryClient.getQueryCache().findAll({
        queryKey: chatCacheKeys.history.detailSession('session-2'),
      }),
    ).toHaveLength(0)
    expect(
      queryClient.getQueryData(
        chatCacheKeys.history.detail({
          sessionId: 'session-3',
        }),
      ),
    ).toEqual({ session: { session_id: 'session-3' } })
  })
})
