const CHAT_STORAGE_PREFIX = 'chat-cache:v1'

export const legacyChatStorageKeys = {
  messages: 'chat-messages',
  sessionId: 'chat-session-id',
  activeDocument: 'chat-active-document',
  userId: 'chat-user-id',
  pdfViewerSession: 'pdf-viewer-session',
} as const

export interface ChatLocalStorageKeys {
  messages: string
  sessionId: string
  activeDocument: string
  pdfViewerSession: string
}

export type LegacyChatMigrationResult =
  | 'noop'
  | 'migrated'
  | 'cleared-legacy-mismatch'
  | 'cleared-legacy-unknown-owner'

function buildChatStorageKey(userId: string, key: string): string {
  return `${CHAT_STORAGE_PREFIX}:${userId}:${key}`
}

export function getChatLocalStorageKeys(userId: string): ChatLocalStorageKeys {
  return {
    messages: buildChatStorageKey(userId, 'messages'),
    sessionId: buildChatStorageKey(userId, 'session-id'),
    activeDocument: buildChatStorageKey(userId, 'active-document'),
    pdfViewerSession: buildChatStorageKey(userId, 'pdf-viewer-session'),
  }
}

export function listNamespacedChatLocalStorageKeys(storage: Storage = window.localStorage): string[] {
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
  Object.values(legacyChatStorageKeys).forEach((key) => storage.removeItem(key))
}

export function migrateLegacyChatLocalStorage(
  userId: string,
  storage: Storage = window.localStorage,
): LegacyChatMigrationResult {
  const legacyOwner = storage.getItem(legacyChatStorageKeys.userId)
  const legacyValues = {
    messages: storage.getItem(legacyChatStorageKeys.messages),
    sessionId: storage.getItem(legacyChatStorageKeys.sessionId),
    activeDocument: storage.getItem(legacyChatStorageKeys.activeDocument),
    pdfViewerSession: storage.getItem(legacyChatStorageKeys.pdfViewerSession),
  }

  const hasLegacyState = legacyOwner !== null || Object.values(legacyValues).some((value) => value !== null)
  if (!hasLegacyState) {
    return 'noop'
  }

  if (legacyOwner !== userId) {
    clearLegacyChatLocalStorage(storage)
    return legacyOwner ? 'cleared-legacy-mismatch' : 'cleared-legacy-unknown-owner'
  }

  const scopedKeys = getChatLocalStorageKeys(userId)
  if (legacyValues.messages !== null && storage.getItem(scopedKeys.messages) === null) {
    storage.setItem(scopedKeys.messages, legacyValues.messages)
  }
  if (legacyValues.sessionId !== null && storage.getItem(scopedKeys.sessionId) === null) {
    storage.setItem(scopedKeys.sessionId, legacyValues.sessionId)
  }
  if (legacyValues.activeDocument !== null && storage.getItem(scopedKeys.activeDocument) === null) {
    storage.setItem(scopedKeys.activeDocument, legacyValues.activeDocument)
  }
  if (legacyValues.pdfViewerSession !== null && storage.getItem(scopedKeys.pdfViewerSession) === null) {
    storage.setItem(scopedKeys.pdfViewerSession, legacyValues.pdfViewerSession)
  }

  clearLegacyChatLocalStorage(storage)
  return 'migrated'
}

const chatQueryRoot = ['chat'] as const

export const chatQueryKeys = {
  all: chatQueryRoot,
  user: (userId: string) => [...chatQueryRoot, 'user', userId] as const,
  sessions: (userId: string) => [...chatQueryRoot, 'user', userId, 'sessions'] as const,
  session: (userId: string, sessionId: string) =>
    [...chatQueryRoot, 'user', userId, 'sessions', sessionId] as const,
}
