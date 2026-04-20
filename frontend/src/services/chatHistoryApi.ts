import { readCurationApiError } from '@/features/curation/services/api'
import { normalizeChatHistoryValue } from '@/lib/chatHistoryNormalization'

export interface ChatHistoryActiveDocument {
  id: string
  filename?: string | null
  chunk_count?: number | null
  vector_count?: number | null
  metadata?: Record<string, unknown> | null
}

export interface ChatHistorySessionSummary {
  session_id: string
  title?: string | null
  active_document_id?: string | null
  created_at: string
  updated_at: string
  last_message_at?: string | null
  recent_activity_at: string
}

export interface ChatHistoryMessage {
  message_id: string
  session_id: string
  turn_id?: string | null
  role: string
  message_type: string
  content: string
  payload_json?: Record<string, unknown> | unknown[] | null
  trace_id?: string | null
  created_at: string
}

export interface ChatHistoryListRequest {
  limit?: number
  cursor?: string | null
  query?: string | null
  documentId?: string | null
}

export interface ChatHistoryListResponse {
  total_sessions: number
  limit: number
  query?: string | null
  document_id?: string | null
  next_cursor?: string | null
  sessions: ChatHistorySessionSummary[]
}

export interface ChatHistoryDetailRequest {
  sessionId: string
  messageLimit?: number
  messageCursor?: string | null
}

export interface ChatHistoryDetailResponse {
  session: ChatHistorySessionSummary
  active_document?: ChatHistoryActiveDocument | null
  messages: ChatHistoryMessage[]
  message_limit: number
  next_message_cursor?: string | null
}

export interface RenameChatSessionRequest {
  sessionId: string
  title: string
}

export interface RenameChatSessionResponse {
  session: ChatHistorySessionSummary
}

export interface DeleteChatSessionRequest {
  sessionId: string
}

export interface BulkDeleteChatSessionsRequest {
  sessionIds: string[]
}

export interface BulkDeleteChatSessionsResponse {
  requested_count: number
  deleted_count: number
  deleted_session_ids: string[]
}

interface ChatHistoryFetchOptions {
  expectJson?: boolean
}

function encodeSessionId(sessionId: string): string {
  return encodeURIComponent(sessionId.trim())
}

async function fetchChatHistoryJson<T>(
  path: string,
  init?: RequestInit,
  options: ChatHistoryFetchOptions = {},
): Promise<T> {
  const headers = new Headers(init?.headers)
  if (init?.body && !headers.has('Content-Type')) {
    headers.set('Content-Type', 'application/json')
  }

  const response = await fetch(path, {
    credentials: 'include',
    ...init,
    headers,
  })

  if (!response.ok) {
    throw new Error(await readCurationApiError(response))
  }

  if (options.expectJson === false || response.status === 204) {
    return undefined as T
  }

  return response.json() as Promise<T>
}

export function buildChatHistoryListQueryParams(
  request: ChatHistoryListRequest = {},
): URLSearchParams {
  const params = new URLSearchParams()

  if (typeof request.limit === 'number') {
    params.set('limit', String(request.limit))
  }

  const cursor = normalizeChatHistoryValue(request.cursor)
  if (cursor) {
    params.set('cursor', cursor)
  }

  const query = normalizeChatHistoryValue(request.query)
  if (query) {
    params.set('query', query)
  }

  const documentId = normalizeChatHistoryValue(request.documentId)
  if (documentId) {
    params.set('document_id', documentId)
  }

  return params
}

export function buildChatHistoryDetailQueryParams(
  request: ChatHistoryDetailRequest,
): URLSearchParams {
  const params = new URLSearchParams()

  if (typeof request.messageLimit === 'number') {
    params.set('message_limit', String(request.messageLimit))
  }

  const messageCursor = normalizeChatHistoryValue(request.messageCursor)
  if (messageCursor) {
    params.set('message_cursor', messageCursor)
  }

  return params
}

export async function fetchChatHistoryList(
  request: ChatHistoryListRequest = {},
): Promise<ChatHistoryListResponse> {
  const params = buildChatHistoryListQueryParams(request)
  const query = params.toString()

  return fetchChatHistoryJson<ChatHistoryListResponse>(
    `/api/chat/history${query ? `?${query}` : ''}`,
  )
}

export async function fetchChatHistoryDetail(
  request: ChatHistoryDetailRequest,
): Promise<ChatHistoryDetailResponse> {
  const params = buildChatHistoryDetailQueryParams(request)
  const query = params.toString()

  return fetchChatHistoryJson<ChatHistoryDetailResponse>(
    `/api/chat/history/${encodeSessionId(request.sessionId)}${query ? `?${query}` : ''}`,
  )
}

export async function renameChatSession(
  request: RenameChatSessionRequest,
): Promise<RenameChatSessionResponse> {
  return fetchChatHistoryJson<RenameChatSessionResponse>(
    `/api/chat/session/${encodeSessionId(request.sessionId)}`,
    {
      method: 'PATCH',
      body: JSON.stringify({ title: request.title }),
    },
  )
}

export async function deleteChatSession(
  request: DeleteChatSessionRequest,
): Promise<void> {
  return fetchChatHistoryJson<void>(
    `/api/chat/session/${encodeSessionId(request.sessionId)}`,
    {
      method: 'DELETE',
    },
    { expectJson: false },
  )
}

export async function bulkDeleteChatSessions(
  request: BulkDeleteChatSessionsRequest,
): Promise<BulkDeleteChatSessionsResponse> {
  return fetchChatHistoryJson<BulkDeleteChatSessionsResponse>(
    '/api/chat/session/bulk-delete',
    {
      method: 'POST',
      body: JSON.stringify({
        session_ids: request.sessionIds,
      }),
    },
  )
}
