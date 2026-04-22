import {
  keepPreviousData,
  type QueryKey,
  type UseQueryOptions,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'

import { chatCacheKeys } from '@/lib/chatCacheKeys'
import {
  bulkDeleteChatSessions,
  deleteChatSession,
  fetchChatHistoryDetail,
  fetchChatHistoryList,
  renameChatSession,
  type BulkDeleteChatSessionsRequest,
  type BulkDeleteChatSessionsResponse,
  type ChatHistoryDetailRequest,
  type ChatHistoryDetailResponse,
  type ChatHistoryListRequest,
  type ChatHistoryListResponse,
  type DeleteChatSessionRequest,
  type RenameChatSessionRequest,
  type RenameChatSessionResponse,
} from '@/services/chatHistoryApi'

type ChatHistoryListQueryOptions = Omit<
  UseQueryOptions<ChatHistoryListResponse, Error, ChatHistoryListResponse, QueryKey>,
  'queryKey' | 'queryFn'
>

type ChatHistoryDetailQueryOptions = Omit<
  UseQueryOptions<ChatHistoryDetailResponse, Error, ChatHistoryDetailResponse, QueryKey>,
  'queryKey' | 'queryFn'
>

type ChatHistoryTranscriptQueryOptions = Omit<
  UseQueryOptions<ChatHistoryDetailResponse, Error, ChatHistoryDetailResponse, QueryKey>,
  'queryKey' | 'queryFn'
>

const FULL_TRANSCRIPT_PAGE_SIZE = 200
const FULL_TRANSCRIPT_MAX_PAGES = 50

async function fetchChatHistoryTranscript(
  request: ChatHistoryDetailRequest,
): Promise<ChatHistoryDetailResponse> {
  const sessionId = request.sessionId.trim()
  const messageLimit = request.messageLimit ?? FULL_TRANSCRIPT_PAGE_SIZE
  const messages: ChatHistoryDetailResponse['messages'] = []
  const seenCursors = new Set<string>()

  let nextCursor = request.messageCursor ?? null
  let detailResponse: ChatHistoryDetailResponse | null = null

  for (
    let pageCount = 0;
    pageCount < FULL_TRANSCRIPT_MAX_PAGES;
    pageCount += 1
  ) {
    const page = await fetchChatHistoryDetail({
      sessionId,
      messageLimit,
      messageCursor: nextCursor,
    })

    if (!detailResponse) {
      detailResponse = page
    }

    messages.push(...page.messages)

    if (!page.next_message_cursor) {
      return {
        ...page,
        session: detailResponse.session,
        active_document: detailResponse.active_document,
        messages,
        next_message_cursor: null,
      }
    }

    if (seenCursors.has(page.next_message_cursor)) {
      throw new Error(`Detected repeated chat history cursor for session ${sessionId}`)
    }

    seenCursors.add(page.next_message_cursor)
    nextCursor = page.next_message_cursor
  }

  throw new Error(
    `Exceeded ${FULL_TRANSCRIPT_MAX_PAGES} transcript pages for session ${sessionId}`,
  )
}

export function useChatHistoryListQuery(
  request: ChatHistoryListRequest = {},
  options: ChatHistoryListQueryOptions = {},
) {
  return useQuery({
    queryKey: chatCacheKeys.history.list(request),
    queryFn: () => fetchChatHistoryList(request),
    placeholderData: keepPreviousData,
    ...options,
  })
}

export function useChatHistoryDetailQuery(
  request: ChatHistoryDetailRequest,
  options: ChatHistoryDetailQueryOptions = {},
) {
  return useQuery({
    queryKey: chatCacheKeys.history.detail(request),
    queryFn: () => fetchChatHistoryDetail(request),
    enabled: request.sessionId.trim().length > 0,
    placeholderData: keepPreviousData,
    ...options,
  })
}

export function useChatHistoryTranscriptQuery(
  request: ChatHistoryDetailRequest,
  options: ChatHistoryTranscriptQueryOptions = {},
) {
  return useQuery({
    queryKey: [
      ...chatCacheKeys.history.detailSession(request.sessionId),
      'transcript',
      { messageLimit: request.messageLimit ?? FULL_TRANSCRIPT_PAGE_SIZE },
    ],
    queryFn: () => fetchChatHistoryTranscript(request),
    enabled: request.sessionId.trim().length > 0,
    ...options,
  })
}

export function useRenameChatSessionMutation() {
  const queryClient = useQueryClient()

  return useMutation<RenameChatSessionResponse, Error, RenameChatSessionRequest>({
    mutationFn: renameChatSession,
    onSuccess: async (_response, request) => {
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: chatCacheKeys.history.lists(),
        }),
        queryClient.invalidateQueries({
          queryKey: chatCacheKeys.history.detailSession(request.sessionId),
        }),
      ])
    },
  })
}

export function useDeleteChatSessionMutation() {
  const queryClient = useQueryClient()

  return useMutation<void, Error, DeleteChatSessionRequest>({
    mutationFn: deleteChatSession,
    onSuccess: async (_response, request) => {
      await queryClient.invalidateQueries({
        queryKey: chatCacheKeys.history.lists(),
      })
      queryClient.removeQueries({
        queryKey: chatCacheKeys.history.detailSession(request.sessionId),
      })
    },
  })
}

export function useBulkDeleteChatSessionsMutation() {
  const queryClient = useQueryClient()

  return useMutation<
    BulkDeleteChatSessionsResponse,
    Error,
    BulkDeleteChatSessionsRequest
  >({
    mutationFn: bulkDeleteChatSessions,
    onSuccess: async (response) => {
      await queryClient.invalidateQueries({
        queryKey: chatCacheKeys.history.lists(),
      })

      response.deleted_session_ids.forEach((sessionId) => {
        queryClient.removeQueries({
          queryKey: chatCacheKeys.history.detailSession(sessionId),
        })
      })
    },
  })
}
