import { normalizeChatHistoryValue } from './chatHistoryNormalization'

const CHAT_STORAGE_PREFIX = 'chat-cache:v1'
const CHAT_QUERY_KEY_PREFIX = ['chat'] as const

export const DEFAULT_CHAT_HISTORY_LIST_LIMIT = 20
export const DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT = 100

const LEGACY_CHAT_STORAGE_KEYS = [
  'chat-messages',
  'chat-session-id',
  'chat-active-document',
  'chat-user-id',
  'pdf-viewer-session',
] as const

export interface ChatLocalStorageKeys {
  messages: string
  sessionId: string
  activeDocument: string
  pdfViewerSession: string
}

export interface ChatRenderCacheKeys {
  auditEvents: string
}

export interface ChatHistoryListCacheRequest {
  chatKind: 'assistant_chat' | 'agent_studio' | 'all'
  limit?: number
  cursor?: string | null
  query?: string | null
  documentId?: string | null
}

export interface ChatHistoryDetailCacheRequest {
  sessionId: string
  messageLimit?: number
  messageCursor?: string | null
}

function buildChatStorageKey(userId: string, key: string): string {
  return `${CHAT_STORAGE_PREFIX}:${userId}:${key}`
}

function normalizeCacheSegment(value: string, fieldName: string): string {
  const normalizedValue = value.trim()
  if (!normalizedValue) {
    throw new Error(`${fieldName} is required`)
  }

  return normalizedValue
}

export const chatCacheKeys = {
  all: CHAT_QUERY_KEY_PREFIX,
  history: {
    all: () => [...CHAT_QUERY_KEY_PREFIX, 'history'] as const,
    lists: () => [...chatCacheKeys.history.all(), 'lists'] as const,
    list: (request: ChatHistoryListCacheRequest) =>
      [
        ...chatCacheKeys.history.lists(),
        {
          chatKind: request.chatKind,
          limit: request.limit ?? DEFAULT_CHAT_HISTORY_LIST_LIMIT,
          cursor: normalizeChatHistoryValue(request.cursor),
          query: normalizeChatHistoryValue(request.query),
          documentId: normalizeChatHistoryValue(request.documentId),
        },
      ] as const,
    details: () => [...chatCacheKeys.history.all(), 'details'] as const,
    detailSession: (sessionId: string) =>
      [...chatCacheKeys.history.details(), sessionId.trim()] as const,
    detail: (request: ChatHistoryDetailCacheRequest) =>
      [
        ...chatCacheKeys.history.detailSession(request.sessionId),
        {
          messageLimit: request.messageLimit ?? DEFAULT_CHAT_HISTORY_MESSAGE_LIMIT,
          messageCursor: normalizeChatHistoryValue(request.messageCursor),
        },
      ] as const,
  },
}

export function getChatLocalStorageKeys(userId: string): ChatLocalStorageKeys {
  return {
    messages: buildChatStorageKey(userId, 'messages'),
    sessionId: buildChatStorageKey(userId, 'session-id'),
    activeDocument: buildChatStorageKey(userId, 'active-document'),
    pdfViewerSession: buildChatStorageKey(userId, 'pdf-viewer-session'),
  }
}

export function getChatRenderCacheKeys(userId: string, sessionId: string): ChatRenderCacheKeys {
  const normalizedUserId = normalizeCacheSegment(userId, 'userId')
  const normalizedSessionId = normalizeCacheSegment(sessionId, 'sessionId')

  return {
    auditEvents: buildChatStorageKey(normalizedUserId, `audit-events:${normalizedSessionId}`),
  }
}

export function clearChatRenderCacheForSession(
  userId: string,
  sessionId: string,
  storage: Storage = window.localStorage,
): void {
  const scopedKeys = getChatRenderCacheKeys(userId, sessionId)
  Object.values(scopedKeys).forEach((key) => storage.removeItem(key))
}

function listNamespacedChatLocalStorageKeys(storage: Storage = window.localStorage): string[] {
  const scopedKeys: string[] = []

  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index)
    if (key?.startsWith(`${CHAT_STORAGE_PREFIX}:`)) {
      scopedKeys.push(key)
    }
  }

  return scopedKeys
}

export function clearChatLocalStorageForUser(userId: string, storage: Storage = window.localStorage): void {
  const scopedKeys = getChatLocalStorageKeys(userId)
  Object.values(scopedKeys).forEach((key) => storage.removeItem(key))
}

export function clearAllNamespacedChatLocalStorage(storage: Storage = window.localStorage): void {
  listNamespacedChatLocalStorageKeys(storage).forEach((key) => storage.removeItem(key))
}

export function clearLegacyChatLocalStorage(storage: Storage = window.localStorage): void {
  LEGACY_CHAT_STORAGE_KEYS.forEach((key) => storage.removeItem(key))
}
