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
