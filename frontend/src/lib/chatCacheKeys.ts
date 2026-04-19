const CHAT_STORAGE_PREFIX = 'chat-cache:v1'

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
