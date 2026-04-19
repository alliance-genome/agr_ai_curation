import { beforeEach, describe, expect, it } from 'vitest'

import {
  clearAllNamespacedChatLocalStorage,
  getChatLocalStorageKeys,
  legacyChatStorageKeys,
  migrateLegacyChatLocalStorage,
} from './chatCacheKeys'

describe('chatCacheKeys', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('migrates same-user legacy cache state into namespaced storage once', () => {
    const scopedKeys = getChatLocalStorageKeys('user-123')

    localStorage.setItem(legacyChatStorageKeys.userId, 'user-123')
    localStorage.setItem(legacyChatStorageKeys.messages, '[{"role":"user","content":"hello"}]')
    localStorage.setItem(legacyChatStorageKeys.sessionId, 'session-123')
    localStorage.setItem(legacyChatStorageKeys.activeDocument, '{"id":"doc-123"}')
    localStorage.setItem(legacyChatStorageKeys.pdfViewerSession, '{"documentId":"doc-123"}')

    expect(migrateLegacyChatLocalStorage('user-123')).toBe('migrated')
    expect(localStorage.getItem(scopedKeys.messages)).toBe('[{"role":"user","content":"hello"}]')
    expect(localStorage.getItem(scopedKeys.sessionId)).toBe('session-123')
    expect(localStorage.getItem(scopedKeys.activeDocument)).toBe('{"id":"doc-123"}')
    expect(localStorage.getItem(scopedKeys.pdfViewerSession)).toBe('{"documentId":"doc-123"}')
    expect(localStorage.getItem(legacyChatStorageKeys.messages)).toBeNull()
    expect(localStorage.getItem(legacyChatStorageKeys.sessionId)).toBeNull()
    expect(localStorage.getItem(legacyChatStorageKeys.activeDocument)).toBeNull()
    expect(localStorage.getItem(legacyChatStorageKeys.pdfViewerSession)).toBeNull()
    expect(localStorage.getItem(legacyChatStorageKeys.userId)).toBeNull()
  })

  it('clears legacy cache state when the stored owner does not match the authenticated user', () => {
    const otherUserKeys = getChatLocalStorageKeys('user-999')

    localStorage.setItem(legacyChatStorageKeys.userId, 'user-999')
    localStorage.setItem(legacyChatStorageKeys.messages, '[{"role":"user","content":"other"}]')
    localStorage.setItem(legacyChatStorageKeys.sessionId, 'session-999')
    localStorage.setItem(legacyChatStorageKeys.activeDocument, '{"id":"doc-999"}')
    localStorage.setItem(legacyChatStorageKeys.pdfViewerSession, '{"documentId":"doc-999"}')

    expect(migrateLegacyChatLocalStorage('user-123')).toBe('cleared-legacy-mismatch')
    expect(localStorage.getItem(otherUserKeys.messages)).toBeNull()
    expect(localStorage.getItem(legacyChatStorageKeys.messages)).toBeNull()
    expect(localStorage.getItem(legacyChatStorageKeys.sessionId)).toBeNull()
    expect(localStorage.getItem(legacyChatStorageKeys.activeDocument)).toBeNull()
    expect(localStorage.getItem(legacyChatStorageKeys.pdfViewerSession)).toBeNull()
    expect(localStorage.getItem(legacyChatStorageKeys.userId)).toBeNull()
  })

  it('clears all namespaced chat state in one pass', () => {
    const firstUserKeys = getChatLocalStorageKeys('user-1')
    const secondUserKeys = getChatLocalStorageKeys('user-2')

    localStorage.setItem(firstUserKeys.messages, '[]')
    localStorage.setItem(secondUserKeys.pdfViewerSession, '{"documentId":"doc-2"}')

    clearAllNamespacedChatLocalStorage()

    expect(localStorage.getItem(firstUserKeys.messages)).toBeNull()
    expect(localStorage.getItem(secondUserKeys.pdfViewerSession)).toBeNull()
  })
})
